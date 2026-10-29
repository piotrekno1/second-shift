from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.http import Http404
from django.test import Client
from django.utils import timezone
from ninja import Schema
from thin_mcp import EmptyToolArguments, ToolContext

from secondshift.mcp_server.api import throttle
from secondshift.mcp_server.audit import prune_tool_calls
from secondshift.mcp_server.models import ToolCall
from secondshift.mcp_server.tools_registry import (
    call_request,
    register_tool,
    registry,
)
from secondshift.workspaces.access import Action, current_workspace, require
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace
from secondshift.workspaces.tokens import create_api_token

PROTOCOL = {"MCP-Protocol-Version": "2025-11-25"}


class SlugResult(Schema):
    slug: str


@pytest.fixture
def test_tools() -> Iterator[None]:
    """Tools that exist only for these tests."""

    @register_tool(
        name="test_workspace",
        description="The token's workspace.",
        arguments_model=EmptyToolArguments,
        result_model=SlugResult,
        read_only=True,
    )
    def workspace(context: ToolContext, arguments: EmptyToolArguments) -> SlugResult:
        found = current_workspace(call_request(context))
        assert found is not None
        return SlugResult(slug=found.slug)

    @register_tool(
        name="test_run",
        description="Needs the run scope.",
        arguments_model=EmptyToolArguments,
        result_model=SlugResult,
        read_only=False,
    )
    def run(context: ToolContext, arguments: EmptyToolArguments) -> SlugResult:
        return SlugResult(slug=require(call_request(context), Action.RUN_JOBS).slug)

    @register_tool(
        name="test_missing",
        description="Looks up something that does not exist.",
        arguments_model=EmptyToolArguments,
        result_model=SlugResult,
        read_only=True,
    )
    def missing(context: ToolContext, arguments: EmptyToolArguments) -> SlugResult:
        raise Http404("private detail")

    @register_tool(
        name="test_broken",
        description="Fails unexpectedly.",
        arguments_model=EmptyToolArguments,
        result_model=SlugResult,
        read_only=True,
    )
    def broken(context: ToolContext, arguments: EmptyToolArguments) -> SlugResult:
        raise RuntimeError("private detail")

    names = ("test_workspace", "test_run", "test_missing", "test_broken")
    yield
    for name in names:
        registry.tools.pop(name, None)


@pytest.fixture
def owner(db: object) -> AbstractUser:
    person = get_user_model().objects.create_user(username="owner", email="o@x.dev")
    create_workspace("Acme", "acme", person)
    return person


def token(person: AbstractUser, scope: str, slug: str = "acme") -> str:
    membership = WorkspaceMembership.objects.get(user=person, workspace__slug=slug)
    return create_api_token(membership, "Laptop", scope, 30).secret


def post(secret: str | None, body: dict[str, Any], client: Client | None = None) -> Any:
    headers = {**PROTOCOL}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return (client or Client(raise_request_exception=False)).post(
        "/mcp", data=body, content_type="application/json", headers=headers
    )


def call(secret: str, name: str, client: Client | None = None) -> Any:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }
    return post(secret, body, client)


def test_requests_without_a_valid_token_are_refused(owner: AbstractUser) -> None:
    for secret in (None, "ss_wrong"):
        response = post(secret, {"jsonrpc": "2.0", "id": 1, "method": "ping"})

        assert response.status_code == 401
        assert response["WWW-Authenticate"] == "Bearer"


def test_a_client_can_initialize_and_list_tools(
    owner: AbstractUser, test_tools: None
) -> None:
    secret = token(owner, "read")
    initialized = post(
        secret,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )

    assert initialized.status_code == 200
    result = initialized.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "secondshift"
    listed = post(secret, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    assert tools["test_workspace"]["annotations"]["readOnlyHint"] is True
    assert tools["test_run"]["annotations"]["readOnlyHint"] is False


def test_tools_work_in_the_token_s_workspace(
    owner: AbstractUser, test_tools: None
) -> None:
    create_workspace("Beta", "beta", owner)

    response = call(token(owner, "read", slug="beta"), "test_workspace")

    assert response.json()["result"]["structuredContent"] == {"slug": "beta"}


def test_scope_and_lookup_errors_are_tool_errors(
    owner: AbstractUser, test_tools: None
) -> None:
    read = token(owner, "read")

    denied = call(read, "test_run").json()["result"]
    missing = call(read, "test_missing").json()["result"]

    assert denied["isError"] is True
    assert denied["content"][0]["text"] == "This API token cannot run jobs."
    assert missing["isError"] is True
    assert missing["content"][0]["text"] == "Not found in this token's workspace."
    assert call(token(owner, "run"), "test_run").json()["result"]["structuredContent"]


def test_unexpected_errors_are_hidden(owner: AbstractUser, test_tools: None) -> None:
    response = call(token(owner, "read"), "test_broken")

    assert response.status_code == 500
    assert response.json()["error"]["message"] == "Internal error."
    assert "private detail" not in response.content.decode()


def test_each_tool_call_is_audited_without_arguments(
    owner: AbstractUser, test_tools: None
) -> None:
    secret = token(owner, "read")

    call(secret, "test_workspace")
    call(secret, "test_run")
    post(secret, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})

    calls = ToolCall.objects.order_by("pk")
    assert [(item.tool, item.outcome, item.user) for item in calls] == [
        ("test_workspace", "ok", owner),
        ("test_run", "error", owner),
    ]
    assert calls[0].workspace == Workspace.objects.get(slug="acme")
    assert calls[0].token is not None


def test_the_rate_limit_is_per_person_across_tokens(
    owner: AbstractUser, test_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(throttle, "num_requests", 2)
    first, second = token(owner, "read"), token(owner, "read")
    other = get_user_model().objects.create_user(username="other", email="t@x.dev")
    WorkspaceMembership.objects.create(
        workspace=Workspace.objects.get(slug="acme"), user=other
    )

    assert call(first, "test_workspace").status_code == 200
    assert call(second, "test_workspace").status_code == 200
    limited = call(first, "test_workspace")

    assert limited.status_code == 429
    assert 1 <= int(limited["Retry-After"]) <= 60
    assert limited.json()["error"]["message"] == "Too many requests."
    assert call(token(other, "read"), "test_workspace").status_code == 200
    assert ToolCall.objects.count() == 3


def test_browser_csrf_checks_do_not_apply(
    owner: AbstractUser, test_tools: None
) -> None:
    client = Client(enforce_csrf_checks=True, raise_request_exception=False)

    assert call(token(owner, "read"), "test_workspace", client).status_code == 200


def test_only_post_is_served(owner: AbstractUser) -> None:
    response = Client().get(
        "/mcp", headers={"Authorization": f"Bearer {token(owner, 'read')}"}
    )

    assert response.status_code == 405


def test_old_tool_calls_are_pruned_with_runs(
    owner: AbstractUser, test_tools: None
) -> None:
    secret = token(owner, "read")
    call(secret, "test_workspace")
    call(secret, "test_workspace")
    ToolCall.objects.filter(pk=ToolCall.objects.order_by("pk").first().pk).update(
        created_at=timezone.now() - timedelta(days=91)
    )

    assert prune_tool_calls(90) == 1
    assert ToolCall.objects.count() == 1
