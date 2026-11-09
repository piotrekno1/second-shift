"""GitHub MCP connections that borrow each repository's source token."""

import json
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from django.contrib.auth import get_user_model

from secondshift.config.constants import GITHUB_MCP_URL
from secondshift.connections.external import MCPKind
from secondshift.connections.mcp import github as github_mcp
from secondshift.connections.mcp.base import SOURCE_AUTH, MCPError
from secondshift.connections.mcp.client import (
    create_mcp_connection,
    update_mcp_connection,
)
from secondshift.connections.models import Connection
from secondshift.connections.repository_ref import RepositoryRef
from secondshift.connections.serialization import serialize_connection
from secondshift.github import app, credentials
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace

PAT = "github_pat_" + "a" * 40
TOOLS = {
    name: {
        "description": name,
        "inputSchema": {
            "type": "object",
            "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}},
        },
    }
    for name in ("list_commits", "get_commit")
}


@pytest.fixture
def workspace(db: object) -> Workspace:
    user = get_user_model().objects.create_user(username="owner")
    return create_workspace(name="Test", slug="test", owner=user)


@pytest.fixture
def mcp_tokens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A fake GitHub MCP server. Records the bearer token of each request."""
    tokens: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == GITHUB_MCP_URL
        message = json.loads(request.content)
        tokens.append(request.headers["Authorization"].removeprefix("Bearer "))
        if "id" not in message:
            return httpx.Response(202)
        match message["method"]:
            case "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "github-fixture", "version": "1"},
                }
            case "tools/list":
                result = {"tools": [{"name": n, **s} for n, s in TOOLS.items()]}
            case "tools/call":
                secret = tokens[-1]
                result = {
                    "content": [{"type": "text", "text": f"commit abc {secret}"}],
                    "isError": False,
                }
            case _:
                raise AssertionError(message)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
        )

    monkeypatch.setattr(
        "secondshift.connections.mcp.client.MCPTransport",
        lambda: httpx.MockTransport(handle),
    )
    return tokens


def token_source(workspace: Workspace) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.API_KEY,
        external_account_id="token:someone",
        display_name="GitHub token",
        access_token=PAT,
    )


def app_source(workspace: Workspace) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.MANAGED,
        external_account_id="42",
        display_name="GitHub App",
    )


def repository(workspace: Workspace, source: Connection | None) -> RepositoryRef:
    return RepositoryRef(workspace.pk, "owner/project", 7, source)


def borrowing(workspace: Workspace) -> Connection:
    return create_mcp_connection(
        workspace, "github", "github", "GitHub", "", auth=SOURCE_AUTH
    )


def test_a_borrowing_connection_stores_no_token(
    workspace: Workspace, mcp_tokens: list[str]
) -> None:
    token_source(workspace)
    connection = borrowing(workspace)
    assert connection.auth_strategy == Connection.AuthStrategy.MANAGED
    assert connection.access_token == ""
    assert set(connection.available_tools) == set(TOOLS)
    # Tools were listed with the workspace's source token.
    assert set(mcp_tokens) == {PAT}
    assert serialize_connection(connection).auth == SOURCE_AUTH


def test_borrowing_needs_a_source_connection(
    workspace: Workspace, mcp_tokens: list[str]
) -> None:
    with pytest.raises(MCPError, match="Connect GitHub"):
        borrowing(workspace)
    assert mcp_tokens == []


def test_borrowing_refuses_a_pasted_token(workspace: Workspace) -> None:
    with pytest.raises(MCPError, match="not a token"):
        create_mcp_connection(
            workspace, "github", "github", "GitHub", PAT, auth=SOURCE_AUTH
        )


def test_only_github_can_borrow_sources(workspace: Workspace) -> None:
    with pytest.raises(MCPError, match="does not accept"):
        create_mcp_connection(
            workspace,
            "sentry",
            "sentry",
            "Sentry",
            "",
            {"organization": "acme"},
            auth=SOURCE_AUTH,
        )


def test_editing_a_borrowing_connection_refuses_a_token(
    workspace: Workspace, mcp_tokens: list[str]
) -> None:
    token_source(workspace)
    connection = borrowing(workspace)
    with pytest.raises(MCPError, match="not a token"):
        update_mcp_connection(connection, None, PAT, None)


def test_a_repository_without_a_source_cannot_borrow(
    workspace: Workspace, mcp_tokens: list[str]
) -> None:
    token_source(workspace)
    connection = borrowing(workspace)
    # A local checkout with a GitHub origin has a name but no source.
    local = RepositoryRef(workspace.pk, "owner/project")
    access = MCPKind().access(connection, local)
    assert access.ok is False
    assert access.reason == "mcp_source_missing"


def test_a_call_uses_the_repository_token_and_redacts_it(
    workspace: Workspace, mcp_tokens: list[str]
) -> None:
    source = token_source(workspace)
    connection = borrowing(workspace)
    repo = repository(workspace, source)
    kind = MCPKind()
    access = kind.access(connection, repo)
    assert access.ok
    frozen = kind.freeze(connection, access.scope, {"list_commits"})
    authorization = kind.authorize(connection, frozen, repo, "list_commits")

    result = kind.call(authorization, "list_commits", {})

    assert mcp_tokens[-1] == PAT
    assert PAT not in json.dumps(result)


def test_an_app_repository_gets_a_token_for_it_alone(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = app_source(workspace)
    repo = repository(workspace, source)
    monkeypatch.setattr(
        app, "installation_permissions", lambda installation: {"contents": "read"}
    )
    mint = Mock(return_value="ghs_minted")
    monkeypatch.setattr(app, "installation_token", mint)

    assert github_mcp.github_source_token(repo) == "ghs_minted"

    # Only the permissions the installation granted, and read access only.
    mint.assert_called_once_with("42", 7, {"contents": "read"})


def test_app_tokens_never_ask_for_write_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app,
        "installation_permissions",
        lambda installation: {
            "contents": "write",
            "pull_requests": "write",
            "actions": "read",
            "administration": "write",
        },
    )
    mint = Mock(return_value="ghs_minted")
    monkeypatch.setattr(app, "installation_token", mint)

    credentials.read_token_for(
        credentials.AppInstallation("42"), 7, github_mcp.GITHUB_TOOL_PERMISSIONS
    )

    assert mint.call_args.args[2] == {
        "contents": "read",
        "pull_requests": "read",
        "actions": "read",
    }


def test_a_revoked_source_stops_the_call(
    workspace: Workspace, mcp_tokens: list[str]
) -> None:
    source = token_source(workspace)
    connection = borrowing(workspace)
    repo = repository(workspace, source)
    kind = MCPKind()
    access = kind.access(connection, repo)
    frozen = kind.freeze(connection, access.scope, {"list_commits"})
    authorization = kind.authorize(connection, frozen, repo, "list_commits")
    Connection.objects.filter(pk=source.pk).update(
        status=Connection.Status.REVOKED, access_token=""
    )
    source.refresh_from_db()

    with pytest.raises(MCPError, match="disconnected"):
        kind.call(authorization, "list_commits", {})
