import json
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db import connection as database
from django.test import Client

from secondshift.connections import agents as agent_logins
from secondshift.connections.agents import (
    AgentConnectionError,
    retry_credential_cleanups,
    save_agent_connection,
    validate_agent_auth,
)
from secondshift.connections.models import AgentConnection, AgentCredentialCleanup
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace, WorkspaceMembership

TOKEN = "sk-ant-oat01-test-subscription-token"
IMAGE = "example/ss-agent@sha256:" + "a" * 64


@pytest.fixture
def agent_login(settings: Any) -> tuple[Workspace, AgentConnection, User]:
    settings.AGENT_IMAGE = IMAGE
    settings.AGENT_BROKER_URL = "http://gateway:8001/gateway/agent/mcp"
    owner = get_user_model().objects.create_user(username="agent-owner")
    workspace = create_workspace(name="Agents", slug="agents", owner=owner)
    login = save_agent_connection(workspace, owner, "claude_code", "Personal", TOKEN)
    return workspace, login, owner


def runner_cleanup(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None = None
) -> Mock:
    runner = Mock()
    if error:
        runner.send.side_effect = error
    else:
        runner.send.return_value = Mock(status_code=204)
    monkeypatch.setattr(agent_logins, "get_runner_client", lambda: runner)
    return runner


def test_auth_import_discards_config_and_rejects_api_keys() -> None:
    with pytest.raises(AgentConnectionError):
        validate_agent_auth("claude_code", "sk-ant-api-key")
    auth = {
        "tokens": {
            key: key
            for key in ("id_token", "access_token", "refresh_token", "account_id")
        },
        "endpoint": "https://evil.example",
        "OPENAI_API_KEY": "secret",
    }
    clean = json.loads(validate_agent_auth("codex", json.dumps(auth)))
    assert "endpoint" not in clean
    assert "OPENAI_API_KEY" not in clean


@pytest.mark.django_db
def test_logins_need_cli_execution_to_be_configured(settings: Any) -> None:
    settings.AGENT_IMAGE = ""
    owner = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Plain", slug="plain", owner=owner)
    with pytest.raises(AgentConnectionError, match="not configured"):
        save_agent_connection(workspace, owner, "claude_code", "", TOKEN)


@pytest.mark.django_db
def test_login_is_stored_encrypted(
    agent_login: tuple[Workspace, AgentConnection, User],
) -> None:
    _, login, _ = agent_login
    assert AgentConnection.objects.get(pk=login.pk).auth_data == TOKEN
    with database.cursor() as cursor:
        cursor.execute("SELECT auth_data FROM connections_agentconnection")
        assert TOKEN not in cursor.fetchone()[0]


@pytest.mark.django_db
def test_api_never_returns_auth_and_checks_owner(
    agent_login: tuple[Workspace, AgentConnection, User], client: Client
) -> None:
    workspace, login, owner = agent_login
    client.force_login(owner)
    response = client.get("/api/agent-connections")
    assert response.status_code == 200
    assert response.json()[0]["can_manage"] is True
    assert TOKEN not in response.content.decode()
    response = client.post(
        "/api/agent-connections",
        data=json.dumps({"backend": "claude_code", "auth": TOKEN}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert TOKEN not in response.content.decode()
    other = get_user_model().objects.create_user(username="other-owner")
    WorkspaceMembership.objects.create(
        workspace=workspace, user=other, role=WorkspaceMembership.Role.OWNER
    )
    client.force_login(other)
    assert client.get("/api/agent-connections").json()[0]["can_manage"] is False
    assert client.delete(f"/api/agent-connections/{login.pk}").status_code == 404


@pytest.mark.django_db
def test_reconnect_rotates_the_runner_credential_key(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, login, owner = agent_login
    runner = runner_cleanup(monkeypatch)
    old_key = login.credential_key
    client.force_login(owner)
    response = client.post(
        f"/api/agent-connections/{login.pk}",
        data=json.dumps({"backend": "claude_code", "auth": TOKEN + "2"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    login.refresh_from_db()
    assert login.connected is True
    assert login.credential_key != old_key
    runner.send.assert_called_once_with(
        "DELETE", f"/v1/agent-credentials/{old_key.hex}"
    )
    response = client.post(
        f"/api/agent-connections/{login.pk}",
        data=json.dumps({"backend": "codex", "auth": TOKEN}),
        content_type="application/json",
    )
    assert response.status_code == 422


@pytest.mark.django_db
def test_disconnect_removes_the_runner_copy(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, login, owner = agent_login
    runner = runner_cleanup(monkeypatch)
    client.force_login(owner)

    response = client.delete(f"/api/agent-connections/{login.pk}")

    assert response.status_code == 204
    runner.send.assert_called_once_with(
        "DELETE", f"/v1/agent-credentials/{login.credential_key.hex}"
    )
    assert not AgentCredentialCleanup.objects.exists()
    assert (
        client.get("/api/agent-connections").json()[0]["runner_cleanup_pending"]
        is False
    )


@pytest.mark.django_db
def test_a_copy_the_runner_kept_is_recorded_and_removed_later(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, login, owner = agent_login
    runner_cleanup(monkeypatch, RunnerProtocolError("unavailable"))
    client.force_login(owner)

    response = client.delete(f"/api/agent-connections/{login.pk}")

    assert response.status_code == 204
    login.refresh_from_db()
    assert login.connected is False
    assert login.auth_data == ""
    cleanup = AgentCredentialCleanup.objects.get()
    assert (cleanup.credential_key, cleanup.attempts) == (login.credential_key, 1)
    assert (
        client.get("/api/agent-connections").json()[0]["runner_cleanup_pending"] is True
    )

    assert retry_credential_cleanups() == 0
    cleanup.refresh_from_db()
    assert cleanup.attempts == 2

    runner = runner_cleanup(monkeypatch)
    assert retry_credential_cleanups() == 1
    runner.send.assert_called_once_with(
        "DELETE", f"/v1/agent-credentials/{login.credential_key.hex}"
    )
    assert not AgentCredentialCleanup.objects.exists()
    assert (
        client.get("/api/agent-connections").json()[0]["runner_cleanup_pending"]
        is False
    )


@pytest.mark.django_db
def test_reconnect_works_while_the_runner_is_down(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, login, owner = agent_login
    runner_cleanup(monkeypatch, RunnerProtocolError("unavailable"))
    old_key = login.credential_key
    client.force_login(owner)

    response = client.post(
        f"/api/agent-connections/{login.pk}",
        data=json.dumps({"backend": "claude_code", "auth": TOKEN + "2"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    login.refresh_from_db()
    assert login.connected is True
    assert login.auth_data == TOKEN + "2"
    assert login.credential_key != old_key
    assert AgentCredentialCleanup.objects.get().credential_key == old_key


@pytest.mark.django_db
def test_a_refused_reconnect_leaves_nothing_to_clean_up(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, login, owner = agent_login
    runner = runner_cleanup(monkeypatch)
    old_key = login.credential_key
    client.force_login(owner)

    response = client.post(
        f"/api/agent-connections/{login.pk}",
        data=json.dumps({"backend": "codex", "auth": TOKEN}),
        content_type="application/json",
    )

    assert response.status_code == 422
    login.refresh_from_db()
    assert (login.connected, login.credential_key) == (True, old_key)
    assert not AgentCredentialCleanup.objects.exists()
    runner.send.assert_not_called()
