import json
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from secondshift.config.constants import GITHUB_MCP_TOOLS, GITHUB_MCP_URL
from secondshift.connections.bindings import bind
from secondshift.connections.mcp.base import MCPError, MCPRequestError
from secondshift.connections.mcp.client import (
    create_mcp_connection,
    discover_tools,
    mcp_request,
    validate_mcp_connection,
    validate_tool_schema,
)
from secondshift.connections.mcp.github import (
    github_tool_error,
    narrowed_github_schema,
    scoped_github_arguments,
)
from secondshift.connections.mcp.transport import BoundedStream
from secondshift.connections.models import Connection
from secondshift.jobs.manifest import load_manifest
from secondshift.workspaces.services import create_workspace

TOOL_SCHEMAS = {
    "search_pull_requests": {
        "description": "Search PRs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "owner": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    "pull_request_read": {
        "description": "Read PRs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string"},
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "pullNumber": {"type": "integer"},
            },
            "required": ["method", "owner", "repo", "pullNumber"],
        },
    },
}

for tool_name in ("list_commits", "get_commit"):
    TOOL_SCHEMAS[tool_name] = {
        "description": tool_name,
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "sha": {"type": "string"},
            },
            "required": ["owner", "repo"],
        },
    }


@pytest.fixture
def mcp_http(monkeypatch) -> list[dict[str, Any]]:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == GITHUB_MCP_URL
        assert request.headers["Authorization"] == "Bearer github_pat_test_token"
        assert request.headers["X-MCP-Readonly"] == "true"
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        calls.append(message)
        if "id" not in message:
            return httpx.Response(202)
        match message["method"]:
            case "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "github-fixture", "version": "1"},
                }
            case "tools/list":
                result = {
                    "tools": [
                        {"name": name, **schema}
                        for name, schema in TOOL_SCHEMAS.items()
                    ]
                }
            case "tools/call":
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": "Merged PR #1 https://github.com/example/project/pull/1 github_pat_test_token",
                        }
                    ],
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
    return calls


@pytest.fixture
def github_connection() -> Connection:
    return Connection(
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        endpoint_url=GITHUB_MCP_URL,
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token="github_pat_test_token",
    )


def test_real_sdk_initialize_discover_call_and_redact(
    mcp_http, github_connection
) -> None:
    tools = discover_tools(github_connection)
    assert set(tools) == set(TOOL_SCHEMAS)
    result = anyio.run(
        mcp_request,
        github_connection,
        "search_pull_requests",
        {"query": "is:merged"},
    )
    assert "github_pat_test_token" not in json.dumps(result)
    assert "[redacted]" in json.dumps(result)
    assert {call["method"] for call in mcp_http} >= {
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "repo:other/project is:merged"},
        {"query": "is:merged OR is:open"},
        {"query": "x" * 257},
        {"query": "is:merged", "owner": "other"},
        {"query": "is:merged", "repo": "other"},
    ],
)
def test_scope_cannot_be_widened(github_connection, arguments) -> None:
    with pytest.raises(MCPError):
        scoped_github_arguments("example/project", "search_pull_requests", arguments)


def test_scope_is_applied_to_every_search(github_connection) -> None:
    result = scoped_github_arguments(
        "example/project",
        "search_pull_requests",
        {"query": "repo:example/project is:merged"},
    )
    assert result == {
        "query": "repo:example/project is:merged",
        "owner": "example",
        "repo": "project",
    }
    with pytest.raises(MCPError):
        scoped_github_arguments("example/project", "update_pull_request", {})


def test_lowercase_or_is_plain_search_text(github_connection) -> None:
    result = scoped_github_arguments(
        "example/project", "search_pull_requests", {"query": "login or signup"}
    )
    assert result["query"] == "repo:example/project login or signup"


@pytest.mark.django_db
@pytest.mark.parametrize("token", ["ghp_classic_token", "github_test_token", ""])
def test_only_fine_grained_tokens_are_accepted(token: str) -> None:
    owner = get_user_model().objects.create_user(username="token-owner")
    workspace = create_workspace(name="Tokens", slug="tokens", owner=owner)
    with pytest.raises(MCPError, match="fine-grained"):
        create_mcp_connection(workspace, "github", "github", "GitHub", token)
    assert not Connection.objects.filter(workspace=workspace).exists()


@pytest.mark.django_db
def test_unknown_mcp_providers_fail_closed() -> None:
    owner = get_user_model().objects.create_user(username="provider-owner")
    workspace = create_workspace(name="Other", slug="other", owner=owner)
    with pytest.raises(MCPError, match="not supported"):
        create_mcp_connection(workspace, "api", "custom", "Custom", "token")
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.API,
        kind=Connection.Kind.MCP,
        display_name="Custom",
        endpoint_url="https://mcp.example.com/",
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token="token",
    )
    bind(connection, "custom")
    with pytest.raises(MCPError, match="not supported"):
        validate_mcp_connection(connection)


def test_github_connection_must_use_the_github_endpoint(github_connection) -> None:
    github_connection.endpoint_url = "https://mcp.example.com/"
    with pytest.raises(MCPError, match="unavailable"):
        validate_mcp_connection(github_connection)


def test_remote_schema_references_are_rejected() -> None:
    with pytest.raises(MCPError, match="references"):
        validate_tool_schema(
            {"properties": {"secret": {"$ref": "http://169.254.169.254/"}}}
        )


def test_response_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "secondshift.connections.mcp.transport.MCP_RESPONSE_MAX_BYTES", 5
    )

    async def read() -> None:
        stream = BoundedStream(httpx.ByteStream(b"too much"))
        async for chunk in stream:
            assert chunk

    with pytest.raises(MCPError, match="size limit"):
        anyio.run(read)


def test_failures_do_not_expose_token_or_remote_error(
    monkeypatch, github_connection
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="github_pat_test_token secret remote detail")

    monkeypatch.setattr(
        "secondshift.connections.mcp.client.MCPTransport",
        lambda: httpx.MockTransport(handle),
    )
    with pytest.raises(MCPError) as caught:
        discover_tools(github_connection)
    assert "github_pat_test_token" not in str(caught.value)
    assert "secret remote detail" not in str(caught.value)


@pytest.mark.django_db
def test_owner_setup_api_encrypts_token_and_does_not_return_it(
    client: Client, mcp_http
) -> None:
    owner = get_user_model().objects.create_user(username="mcp-owner")
    create_workspace(name="MCP", slug="mcp", owner=owner)
    client.force_login(owner)
    response = client.post(
        "/api/connections/mcp",
        data=json.dumps(
            {
                "provider": "github",
                "binding_key": "github",
                "token": "github_pat_test_token",
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert "github_pat_test_token" not in response.content.decode()
    connection = Connection.objects.get(pk=response.json()["id"])
    assert connection.access_token == "github_pat_test_token"
    assert connection.available_tools == sorted(TOOL_SCHEMAS)
    duplicate = client.post(
        "/api/connections/mcp",
        data=json.dumps(
            {
                "provider": "github",
                "binding_key": "github",
                "token": "github_pat_test_token",
            }
        ),
        content_type="application/json",
    )
    assert duplicate.status_code == 409
    client.logout()
    assert (
        client.post(
            "/api/connections/mcp", data={}, content_type="application/json"
        ).status_code
        == 401
    )


@pytest.mark.parametrize("tool", ["list_commits", "get_commit"])
def test_commit_tools_are_scoped_to_the_job_repository(tool: str) -> None:
    assert scoped_github_arguments("example/project", tool, {}) == {
        "owner": "example",
        "repo": "project",
    }
    with pytest.raises(MCPError, match="repository"):
        scoped_github_arguments("example/project", tool, {"repo": "other"})


@pytest.mark.parametrize(
    ("tool", "method"),
    [
        ("actions_list", "list_workflows"),
        ("actions_list", "list_workflow_runs"),
        ("actions_list", "list_workflow_jobs"),
        ("actions_get", "get_workflow"),
        ("actions_get", "get_workflow_run"),
        ("actions_get", "get_workflow_job"),
    ],
)
def test_actions_read_methods_are_scoped_to_the_job_repository(
    tool: str, method: str
) -> None:
    arguments = {"method": method, "resource_id": "42"}
    assert scoped_github_arguments("example/project", tool, arguments) == {
        **arguments,
        "owner": "example",
        "repo": "project",
    }
    with pytest.raises(MCPError, match="repository"):
        scoped_github_arguments(
            "example/project", tool, {**arguments, "owner": "other"}
        )


@pytest.mark.parametrize(
    ("tool", "method"),
    [
        ("actions_list", "list_workflow_run_artifacts"),
        ("actions_get", "get_workflow_run_logs_url"),
        ("actions_get", "download_workflow_run_artifact"),
        ("actions_get", "get_workflow_run_usage"),
        ("actions_get", None),
    ],
)
def test_actions_methods_with_links_or_artifacts_are_blocked(
    tool: str, method: str | None
) -> None:
    with pytest.raises(MCPError, match="methods"):
        scoped_github_arguments(
            "example/project", tool, {"method": method, "resource_id": "42"}
        )


def test_actions_trigger_is_not_supported() -> None:
    with pytest.raises(MCPError, match="not supported"):
        scoped_github_arguments(
            "example/project", "actions_run_trigger", {"method": "rerun_workflow_run"}
        )


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 300), (50, 50), (5000, 300), (0, 300), (True, 300), ("10", 300)],
)
def test_job_logs_return_content_with_a_line_limit(
    requested: Any, expected: int
) -> None:
    arguments: dict[str, Any] = {"run_id": 7, "failed_only": True}
    if requested is not None:
        arguments["tail_lines"] = requested
    arguments["return_content"] = False
    result = scoped_github_arguments("example/project", "get_job_logs", arguments)
    assert result == {
        "run_id": 7,
        "failed_only": True,
        "owner": "example",
        "repo": "project",
        "return_content": True,
        "tail_lines": expected,
    }


def test_list_commits_cannot_select_another_branch() -> None:
    with pytest.raises(MCPError, match="default branch"):
        scoped_github_arguments("example/project", "list_commits", {"sha": "feature"})


@pytest.mark.django_db
def test_provider_list_describes_github_without_secrets(client: Client) -> None:
    owner = get_user_model().objects.create_user(username="catalog-owner")
    create_workspace(name="Catalog", slug="catalog", owner=owner)
    client.force_login(owner)
    response = client.get("/api/connections/mcp-providers")
    assert response.status_code == 200
    providers = {item["key"]: item for item in response.json()}
    assert set(providers) == {"github", "sentry", "mcp"}
    github = providers["github"]
    assert github["default_binding"] == "github"
    assert github["settings"] == []
    assert [item["key"] for item in providers["sentry"]["settings"]] == [
        "organization",
        "project",
    ]
    for item in providers.values():
        assert "token_pattern" not in item
        assert "headers" not in item
        assert "auth_scheme" not in item


@pytest.mark.django_db
def test_create_rejects_unknown_provider(client: Client) -> None:
    owner = get_user_model().objects.create_user(username="custom-owner")
    create_workspace(name="Custom", slug="custom", owner=owner)
    client.force_login(owner)
    response = client.post(
        "/api/connections/mcp",
        data=json.dumps(
            {"provider": "mcp", "binding_key": "custom", "token": "secret"}
        ),
        content_type="application/json",
    )
    assert response.status_code == 422
    assert not Connection.objects.filter(bindings__key="custom").exists()


ACTIONS_SCHEMAS = {
    "actions_list": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": [
                    "list_workflows",
                    "list_workflow_runs",
                    "list_workflow_jobs",
                    "list_workflow_run_artifacts",
                ],
            },
            "owner": {"type": "string"},
            "repo": {"type": "string"},
        },
        "required": ["method", "owner", "repo"],
    },
    "actions_get": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": [
                    "get_workflow",
                    "get_workflow_run",
                    "get_workflow_job",
                    "get_workflow_run_usage",
                    "get_workflow_run_logs_url",
                    "download_workflow_run_artifact",
                ],
            },
            "resource_id": {"type": "string"},
        },
        "required": ["method", "resource_id"],
    },
    "get_job_logs": {
        "type": "object",
        "properties": {
            "run_id": {"type": "number"},
            "failed_only": {"type": "boolean"},
            "return_content": {"type": "boolean"},
            "tail_lines": {"type": "number", "default": 500},
        },
        "required": ["owner", "repo", "return_content"],
    },
}


def test_model_sees_only_allowed_actions_methods() -> None:
    listed = narrowed_github_schema("actions_list", ACTIONS_SCHEMAS["actions_list"])
    got = narrowed_github_schema("actions_get", ACTIONS_SCHEMAS["actions_get"])
    assert listed["properties"]["method"]["enum"] == [
        "list_workflow_jobs",
        "list_workflow_runs",
        "list_workflows",
    ]
    assert got["properties"]["method"]["enum"] == [
        "get_workflow",
        "get_workflow_job",
        "get_workflow_run",
    ]
    # The server's schema is left as it was.
    assert (
        "list_workflow_run_artifacts"
        in (ACTIONS_SCHEMAS["actions_list"]["properties"]["method"]["enum"])
    )


def test_model_is_not_offered_log_links_or_githubs_line_default() -> None:
    logs = narrowed_github_schema("get_job_logs", ACTIONS_SCHEMAS["get_job_logs"])
    assert "return_content" not in logs["properties"]
    assert logs["required"] == ["owner", "repo"]
    assert "default" not in logs["properties"]["tail_lines"]
    assert "300" in logs["properties"]["tail_lines"]["description"]


def test_other_github_schemas_are_unchanged() -> None:
    schema = TOOL_SCHEMAS["pull_request_read"]["inputSchema"]
    assert narrowed_github_schema("pull_request_read", schema) == schema


def test_discovery_stores_the_narrowed_schema(mcp_http, github_connection) -> None:
    TOOL_SCHEMAS["actions_get"] = {
        "description": "Get Actions resources",
        "inputSchema": ACTIONS_SCHEMAS["actions_get"],
    }
    try:
        tools = discover_tools(github_connection)
    finally:
        del TOOL_SCHEMAS["actions_get"]
    assert tools["actions_get"]["inputSchema"]["properties"]["method"]["enum"] == [
        "get_workflow",
        "get_workflow_job",
        "get_workflow_run",
    ]


@pytest.mark.parametrize(
    ("tool", "text", "expected"),
    [
        (
            "get_job_logs",
            "failed to get job logs: GET https://api.github.com/x: 403 "
            "Resource not accessible by personal access token []",
            "read access to Actions",
        ),
        ("actions_list", "Resource not accessible by integration", "Actions"),
        ("get_commit", "403 Forbidden", "repository permissions"),
        ("get_job_logs", "404 Not Found", None),
    ],
)
def test_github_refusals_name_the_missing_permission(
    tool: str, text: str, expected: str | None
) -> None:
    message = github_tool_error(tool, text)
    if expected is None:
        assert message is None
    else:
        assert expected in message
        assert "api.github.com" not in message


def tool_error_transport(text: str) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        match message["method"]:
            case "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "github-fixture", "version": "1"},
                }
            case "tools/call":
                result = {"content": [{"type": "text", "text": text}], "isError": True}
            case _:
                raise AssertionError(message)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
        )

    return httpx.MockTransport(handle)


def test_tool_refusal_reaches_the_agent_without_remote_text(
    monkeypatch, github_connection
) -> None:
    remote = (
        "403 Resource not accessible by personal access token github_pat_test_token"
    )
    monkeypatch.setattr(
        "secondshift.connections.mcp.client.MCPTransport",
        lambda: tool_error_transport(remote),
    )
    with pytest.raises(MCPRequestError) as caught:
        anyio.run(mcp_request, github_connection, "get_job_logs", {"run_id": 7})
    assert "read access to Actions" in str(caught.value)
    assert "github_pat_test_token" not in str(caught.value)
    assert "personal access token" not in str(caught.value)


def test_other_tool_failures_keep_the_general_message(
    monkeypatch, github_connection
) -> None:
    monkeypatch.setattr(
        "secondshift.connections.mcp.client.MCPTransport",
        lambda: tool_error_transport("something broke at api.github.com"),
    )
    with pytest.raises(MCPRequestError, match="GitHub could not complete") as caught:
        anyio.run(mcp_request, github_connection, "get_commit", {})
    assert "api.github.com" not in str(caught.value)


def test_ci_failure_example_requests_only_built_in_github_tools() -> None:
    manifest = load_manifest(Path("examples/ci-failure-review/.secondshift.yaml"))
    job = manifest.jobs["ci-failure-review"]
    assert Path("examples/ci-failure-review", job.skill).is_file()
    [github] = job.requires.mcps
    assert github.binding == "github"
    assert set(github.tools) <= set(GITHUB_MCP_TOOLS)
    assert {"actions_list", "actions_get", "get_job_logs"} <= set(github.tools)
