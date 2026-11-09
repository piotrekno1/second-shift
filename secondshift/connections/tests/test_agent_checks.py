from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client

from secondshift.connections import agents as agent_logins
from secondshift.connections.agents import save_agent_connection
from secondshift.connections.execution import choose_execution, workspace_execution
from secondshift.connections.models import AgentConnection
from secondshift.connections.tests.test_agent_connections import (
    TOKEN,
)
from secondshift.connections.tests.test_agent_connections import (
    agent_login as agent_login_fixture,
)
from secondshift.runner.agent_credentials import provisioning_public_key
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.workspaces.models import Workspace, WorkspaceMembership

agent_login = agent_login_fixture
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def runner_keys(settings: Any, tmp_path: Any) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"


def fake_runner(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any] | Exception
) -> list[dict[str, Any]]:
    """Answer the provisioning key and the check, and record what was sent."""
    sent: list[dict[str, Any]] = []
    runner = Mock()

    def send(method: str, path: str, **kwargs: Any) -> str:
        sent.append({"method": method, "path": path, **kwargs})
        if isinstance(result, Exception) and path == "/v1/agent-checks":
            raise result
        return path

    def payload(path: str) -> dict[str, Any]:
        if path == "/v1/agent-provisioning-key":
            return {"public_key": provisioning_public_key()}
        assert isinstance(result, dict)
        return result

    runner.send.side_effect = send
    runner.response_payload.side_effect = payload
    monkeypatch.setattr(agent_logins, "get_runner_client", lambda: runner)
    return sent


def check(client: Client, owner: User, login: AgentConnection) -> Any:
    client.force_login(owner)
    return client.post(f"/api/agent-connections/{login.pk}/check")


def test_a_check_sends_only_the_sealed_login_and_keeps_no_cli_text(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, login, owner = agent_login
    sent = fake_runner(
        monkeypatch, {"reason": "completed", "report": "OK", "succeeded": True}
    )
    response = check(client, owner, login)
    assert response.status_code == 200
    last = response.json()["last_check"]
    assert (last["ok"], last["reason"]) == (True, "completed")
    assert last["message"] == "Signed in. Claude Code answered a test message."
    body = sent[-1]["json"]
    assert sent[-1]["path"] == "/v1/agent-checks"
    assert set(body) == {"backend", "credential_key", "auth"}
    assert TOKEN not in str(sent)
    assert "OK" not in str(AgentConnection.objects.get(pk=login.pk).last_check)


@pytest.mark.parametrize(
    ("reason", "ok", "blocks"),
    [
        ("quota_exhausted", True, False),
        ("reconnect_required", False, True),
        ("timed_out", False, False),
        ("something_new", False, False),
    ],
)
def test_only_a_refused_sign_in_stops_runs(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    ok: bool,
    blocks: bool,
) -> None:
    workspace, login, owner = agent_login
    choose_execution(workspace, login=login)
    fake_runner(monkeypatch, {"reason": reason, "succeeded": False})
    last = check(client, owner, login).json()["last_check"]
    assert last["ok"] is ok
    problem = workspace_execution(Workspace.objects.get(pk=workspace.pk)).problem
    assert bool(problem) is blocks
    if blocks:
        assert problem == (
            "The agent login Personal was refused at its last check. Reconnect it."
        )
        # Saving a new login clears the old result.
        save_agent_connection(workspace, owner, "claude_code", "Personal", TOKEN, login)
        assert not workspace_execution(Workspace.objects.get(pk=workspace.pk)).problem


def test_a_busy_runner_saves_nothing(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, login, owner = agent_login
    fake_runner(monkeypatch, RunnerProtocolError("busy"))
    response = check(client, owner, login)
    assert response.status_code == 409
    assert "Try again" in response.json()["detail"]
    assert AgentConnection.objects.get(pk=login.pk).last_check is None


def test_only_the_owner_checks_a_login(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, login, _ = agent_login
    sent = fake_runner(monkeypatch, {"reason": "completed"})
    other = get_user_model().objects.create_user(username="other")
    WorkspaceMembership.objects.create(workspace=workspace, user=other, role="owner")
    assert check(client, other, login).status_code == 404
    admin = get_user_model().objects.create_user(username="admin")
    WorkspaceMembership.objects.create(workspace=workspace, user=admin, role="admin")
    assert check(client, admin, login).status_code == 403
    assert sent == []
