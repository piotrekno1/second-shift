import hashlib
import json
from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.utils import timezone
from pydantic_ai import ModelRetry, RunContext, Tool

from secondshift.connections import agents as agent_logins
from secondshift.connections.models import AgentConnection
from secondshift.connections.tests.test_agent_connections import (
    agent_login as agent_login_fixture,
)
from secondshift.jobs.tests.test_execution_defaults import (
    agent_run as agent_run_fixture,
)
from secondshift.runner.protocol import CommandResult, RunnerProtocolError
from secondshift.runs.execution.agent_broker import (
    AgentAccessError,
    agent_mcp,
    mcp_request,
    tool_result,
)
from secondshift.runs.execution.deadlines import RunTimedOut
from secondshift.runs.models import AgentLease, Run
from secondshift.workspaces.models import WorkspaceMembership

agent_login = agent_login_fixture
agent_run = agent_run_fixture
pytestmark = pytest.mark.django_db


@pytest.fixture
def leased_run(agent_run: tuple[Run, User]) -> tuple[Run, AgentLease, User]:
    run, owner = agent_run
    lease = AgentLease.objects.create(
        run=run,
        session_id="run-1",
        token_hash=hashlib.sha256(b"broker-token").hexdigest(),
    )
    return run, lease, owner


def test_mcp_exposes_and_calls_only_frozen_tools(
    leased_run: tuple[Run, AgentLease, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, lease, _ = leased_run
    calls = []

    def execute(
        session_id: str, call_id: str, argv: list[str], cwd: str, timeout: int
    ) -> CommandResult:
        calls.append((call_id, argv))
        return CommandResult(call_id=call_id, exit_code=0, output="skill text")

    monkeypatch.setattr(
        "secondshift.runs.execution.workspace_tools.execute_runner_command_step",
        execute,
    )
    run.execution_plan["agent"]["tools"] = ["workspace.read_file"]
    run.save()
    listed = mcp_request(lease, {"id": 1, "method": "tools/list"})
    assert [tool["name"] for tool in listed["tools"]] == ["workspace_read_file"]
    call = {"name": "workspace_read_file", "arguments": {"path": "SKILL.md"}}
    first = mcp_request(lease, {"id": 2, "method": "tools/call", "params": call})
    # A reconnected client starts its request IDs again. That is a new call.
    second = mcp_request(lease, {"id": 2, "method": "tools/call", "params": call})
    assert first == second
    assert [argv for _, argv in calls] == [["cat", "--", "SKILL.md"]] * 2
    assert calls[0][0] != calls[1][0]
    with pytest.raises(AgentAccessError):
        tool_result(
            lease, "workspace_run_command", {"argv": ["cat", "/auth/auth.json"]}
        )
    assert (
        tool_result(lease, "workspace_read_file", {"path": "/auth/auth.json"})[
            "isError"
        ]
        is True
    )


def test_the_agent_is_told_how_to_fix_its_arguments(
    leased_run: tuple[Run, AgentLease, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lease, _ = leased_run

    def search(ctx: RunContext[Any], query: str) -> dict[str, Any]:
        raise ModelRetry("Use separate searches instead of OR.")

    monkeypatch.setattr(
        "secondshift.runs.execution.agent_broker.broker_tools",
        lambda run: {"search": Tool(search, takes_ctx=True)},
    )

    assert tool_result(lease, "search", {"query": "a OR b"}) == {
        "content": [{"type": "text", "text": "Use separate searches instead of OR."}],
        "isError": True,
    }


@pytest.mark.parametrize(
    "change", ["canceled", "disconnected", "removed_owner", "deadline", "revoked_lease"]
)
def test_broker_rechecks_access_before_every_call(
    leased_run: tuple[Run, AgentLease, User], change: str
) -> None:
    run, lease, owner = leased_run
    if change == "canceled":
        run.status = Run.Status.CANCELED
        run.save()
    elif change == "disconnected":
        AgentConnection.objects.filter(
            pk=run.job.workspace.default_agent_connection_id
        ).update(connected=False)
    elif change == "removed_owner":
        WorkspaceMembership.objects.filter(
            workspace=run.job.workspace, user=owner
        ).delete()
    elif change == "deadline":
        run.started_at = timezone.now() - timedelta(days=2)
        run.save()
    else:
        lease.active = False
        lease.save()
    with pytest.raises((AgentAccessError, RunTimedOut)):
        mcp_request(lease, {"id": 1, "method": "tools/list"})


def test_broker_stops_at_the_tool_call_budget(
    leased_run: tuple[Run, AgentLease, User],
) -> None:
    run, lease, _ = leased_run
    lease.tool_calls = run.execution_plan["agent"]["limits"]["max_tool_calls"]
    lease.save()
    with pytest.raises(AgentAccessError, match="budget"):
        tool_result(lease, "workspace_read_file", {"path": "SKILL.md"})


def test_broker_auth_does_not_accept_user_or_model_gateway_tokens(
    leased_run: tuple[Run, AgentLease, User],
) -> None:
    factory = RequestFactory()

    def post(body: str, **headers: Any) -> Any:
        return agent_mcp(
            factory.post("/", data=body, content_type="application/json", **headers)
        )

    assert post("{}").status_code == 401
    assert post("{}", HTTP_AUTHORIZATION="Bearer unrelated").status_code == 401
    response = post(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        HTTP_AUTHORIZATION="Bearer broker-token",
    )
    assert response.status_code == 200
    assert json.loads(response.content)["result"]["capabilities"] == {"tools": {}}


def test_disconnect_blocks_the_broker_before_runner_cleanup(
    leased_run: tuple[Run, AgentLease, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, lease, owner = leased_run
    runner = Mock()
    runner.send.side_effect = RunnerProtocolError("unavailable")
    monkeypatch.setattr(agent_logins, "get_runner_client", lambda: runner)
    client.force_login(owner)
    response = client.delete(
        f"/api/agent-connections/{run.job.workspace.default_agent_connection_id}"
    )
    assert response.status_code == 503
    with pytest.raises(AgentAccessError):
        mcp_request(lease, {"id": 1, "method": "ping"})


@pytest.mark.parametrize(
    ("requested", "answered"),
    [
        ("2025-06-18", "2025-06-18"),
        ("2025-11-25", "2025-11-25"),
        ("1999-01-01", "2025-11-25"),
    ],
)
def test_broker_speaks_the_client_protocol_version(
    leased_run: tuple[Run, AgentLease, User], requested: str, answered: str
) -> None:
    _, lease, _ = leased_run
    result = mcp_request(
        lease,
        {"id": 0, "method": "initialize", "params": {"protocolVersion": requested}},
    )
    assert result["protocolVersion"] == answered


def test_unknown_methods_are_not_found_so_clients_fall_back(
    leased_run: tuple[Run, AgentLease, User],
) -> None:
    response = agent_mcp(
        RequestFactory().post(
            "/",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": "probe", "method": "server/discover"}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer broker-token",
        )
    )
    assert json.loads(response.content)["error"]["code"] == -32601
