import json
from typing import Any

import httpx
import pytest
from django.contrib.auth import get_user_model

from secondshift.automations.models import CapabilityRequest, Job, JobGrant
from secondshift.automations.planning import build_execution_plan
from secondshift.config.constants import SENTRY_MCP_URL
from secondshift.core.models import Workspace
from secondshift.integrations.mcp.base import MCPError
from secondshift.integrations.mcp.client import (
    create_mcp_connection,
    validate_mcp_connection,
)
from secondshift.integrations.mcp.providers import mcp_access
from secondshift.integrations.mcp.sentry import SENTRY_MCP, scoped_sentry_arguments
from secondshift.integrations.models import Connection
from secondshift.repositories.models import Repository

TOKEN = "sntryu_test_token"
OBJECT_SCHEMA = {"type": "object", "properties": {"issueId": {"type": "string"}}}
READ_ONLY = {"readOnlyHint": True}


def listed_tools() -> list[dict[str, Any]]:
    return [
        # Allowed and marked read-only: kept.
        {
            "name": "get_sentry_resource",
            "inputSchema": OBJECT_SCHEMA,
            "annotations": READ_ONLY,
        },
        # Allowed but not marked read-only: dropped.
        {"name": "find_projects", "inputSchema": OBJECT_SCHEMA},
        # Read-only but not on the allowlist: dropped.
        {
            "name": "search_replays",
            "inputSchema": OBJECT_SCHEMA,
            "annotations": READ_ONLY,
        },
        # Writes or runs other tools: dropped.
        {"name": "update_issue", "inputSchema": OBJECT_SCHEMA},
        {"name": "execute_sentry_tool", "inputSchema": OBJECT_SCHEMA},
    ]


@pytest.fixture
def sentry_http(monkeypatch) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        match message["method"]:
            case "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "sentry-fixture", "version": "1"},
                }
            case "tools/list":
                result = {"tools": listed_tools()}
            case _:
                raise AssertionError(message)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
        )

    monkeypatch.setattr(
        "secondshift.integrations.mcp.client.MCPTransport",
        lambda: httpx.MockTransport(handle),
    )
    return requests


@pytest.fixture
def workspace(db) -> Workspace:
    owner = get_user_model().objects.create_user(username="sentry-owner")
    return Workspace.objects.create(name="Sentry", slug="sentry", owner=owner)


def test_connect_scopes_endpoint_auth_and_read_only_tools(
    workspace: Workspace, sentry_http: list[httpx.Request]
) -> None:
    connection = create_mcp_connection(
        workspace,
        "sentry",
        "sentry",
        "",
        TOKEN,
        {"organization": "acme", "project": "backend"},
    )
    assert connection.endpoint_url == f"{SENTRY_MCP_URL}/acme/backend"
    assert connection.display_name == "Sentry"
    assert connection.metadata["target"] == "Sentry MCP · acme/backend"
    assert connection.metadata["settings"] == {
        "organization": "acme",
        "project": "backend",
    }
    assert connection.available_tools == ["get_sentry_resource"]
    assert {str(request.url) for request in sentry_http} == {connection.endpoint_url}
    assert {request.headers["Authorization"] for request in sentry_http} == {
        f"Sentry-Bearer {TOKEN}"
    }


def test_organization_only_connection(
    workspace: Workspace, sentry_http: list[httpx.Request]
) -> None:
    connection = create_mcp_connection(
        workspace, "sentry", "sentry", "", TOKEN, {"organization": "acme"}
    )
    assert connection.endpoint_url == f"{SENTRY_MCP_URL}/acme"
    assert connection.metadata["target"] == "Sentry MCP · acme"


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({}, "organization"),
        ({"organization": "acme/other"}, "not valid"),
        ({"organization": "../admin"}, "not valid"),
        ({"organization": "acme", "project": "a b"}, "not valid"),
        ({"organization": "acme", "region": "us"}, "does not accept"),
    ],
)
def test_settings_are_validated_before_any_request(
    workspace: Workspace,
    sentry_http: list[httpx.Request],
    settings: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(MCPError, match=message):
        create_mcp_connection(workspace, "sentry", "sentry", "", TOKEN, settings)
    assert sentry_http == []
    assert not Connection.objects.filter(workspace=workspace).exists()


def test_endpoint_must_match_saved_settings() -> None:
    connection = Connection(
        provider=Connection.Provider.SENTRY,
        kind=Connection.Kind.MCP,
        endpoint_url=f"{SENTRY_MCP_URL}/other",
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token=TOKEN,
        metadata={"settings": {"organization": "acme"}},
    )
    with pytest.raises(MCPError, match="unavailable"):
        validate_mcp_connection(connection)
    connection.endpoint_url = f"{SENTRY_MCP_URL}/acme"
    assert validate_mcp_connection(connection) is SENTRY_MCP


@pytest.mark.parametrize(
    ("scope", "tool", "arguments", "message"),
    [
        ("acme/backend", "search_errors", {"projectSlug": "frontend"}, "approved"),
        ("acme/backend", "search_issues", {"projectSlugOrId": "42"}, "approved"),
        ("acme", "find_projects", {"regionUrl": "https://evil.example"}, "regionUrl"),
        ("acme", "search_issues", {"query": "x" * 257}, "256 characters"),
        (
            "acme",
            "get_sentry_resource",
            {"url": "https://sentry.io/organizations/other/issues/1/"},
            "resourceId",
        ),
        (
            "acme",
            "get_sentry_resource",
            {"resourceType": "replay", "resourceId": "abc"},
            "issues, events, and traces",
        ),
        ("acme", "get_sentry_resource", {"resourceType": "issue"}, "resourceId"),
        ("acme", "update_issue", {"issueId": "1"}, "not supported"),
        ("acme", "execute_sentry_tool", {"name": "update_issue"}, "not supported"),
    ],
)
def test_arguments_cannot_leave_the_scope(
    scope: str, tool: str, arguments: dict, message: str
) -> None:
    with pytest.raises(MCPError, match=message):
        scoped_sentry_arguments(scope, tool, arguments)


@pytest.mark.parametrize(
    ("scope", "tool", "arguments"),
    [
        ("acme", "search_issues", {"query": "is:unresolved", "projectSlugOrId": "web"}),
        (
            "acme/backend",
            "search_errors",
            {"query": "errors today", "projectSlug": "backend"},
        ),
        (
            "acme",
            "get_sentry_resource",
            {"resourceType": "issue", "resourceId": "WEB-1"},
        ),
        ("acme", "find_projects", {"regionUrl": None}),
    ],
)
def test_arguments_inside_the_scope_pass_unchanged(
    scope: str, tool: str, arguments: dict
) -> None:
    assert scoped_sentry_arguments(scope, tool, arguments) == arguments


def test_access_text_and_frozen_plan_follow_the_connection(
    workspace: Workspace,
) -> None:
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", config_sha="a" * 40
    )
    Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Model",
        default_model="claude-opus-5-5",
        is_default=True,
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="triage",
        name="Triage",
        skill_path=".agents/skills/triage/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.SENTRY,
        kind=Connection.Kind.MCP,
        binding_key="sentry",
        display_name="Sentry",
        endpoint_url=f"{SENTRY_MCP_URL}/acme",
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token=TOKEN,
        available_tools=["get_sentry_resource"],
        metadata={
            "settings": {"organization": "acme"},
            "tools": {"get_sentry_resource": {"inputSchema": OBJECT_SCHEMA}},
        },
    )
    request = CapabilityRequest.objects.create(
        job=job,
        binding="sentry",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["get_sentry_resource"],
    )
    JobGrant.objects.create(
        capability_request=request,
        connection=connection,
        status=JobGrant.Status.APPROVED,
        allowed_tools=["get_sentry_resource"],
    )

    access = mcp_access(connection, repository)
    assert access.ok
    assert access.text == "Allow these tools to read all Sentry projects in acme."
    plan = build_execution_plan(job)
    assert plan["ready"] is True
    assert plan["capabilities"][0]["mcp"]["provider"] == "sentry"
    assert plan["capabilities"][0]["mcp"]["scope"] == "acme"

    connection.metadata = {**connection.metadata, "settings": {}}
    assert not mcp_access(connection, repository).ok
    connection.save()
    plan = build_execution_plan(job)
    assert plan["capabilities"][0]["reasons"] == ["sentry_organization_unknown"]
