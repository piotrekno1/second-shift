"""Agent logins with several runners: every copy is removed, checks run anywhere."""

from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from django.contrib.auth.models import User

from secondshift.connections import agents as agent_logins
from secondshift.connections.agents import (
    AgentConnectionError,
    check_agent_connection,
    disconnect_agent_connection,
    retry_credential_cleanups,
)
from secondshift.connections.models import AgentConnection, AgentCredentialCleanup
from secondshift.runner.tests.fakes import agent_runner, closable_mock
from secondshift.runner_protocol.sessions import RunnerProtocolError
from secondshift.runs.dao import login_home, set_login_home
from secondshift.runs.models import LoginHome, Runner
from secondshift.runs.runners import (
    add_runner,
    change_runner_state,
    sync_configured_runner,
)
from secondshift.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

TOKEN = "t" * 32


class Runners:
    """Fake clients by runner name, recording each DELETE they were sent."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.down: set[str] = set()
        self.asked: list[str] = []
        monkeypatch.setattr(agent_logins, "runner_client", self.client)

    def client(self, runner: Runner) -> MagicMock:
        fake = closable_mock()

        def send(method: str, path: str, **kwargs: Any) -> Mock:
            self.asked.append(runner.name)
            if runner.name in self.down:
                raise RunnerProtocolError("unreachable")
            return Mock(status_code=204)

        fake.send.side_effect = send
        return fake


@pytest.fixture
def runners(monkeypatch: pytest.MonkeyPatch) -> Runners:
    add_runner("runner-b", "https://runner-b:8002", TOKEN)
    return Runners(monkeypatch)


def test_disconnect_removes_the_copy_from_every_runner(
    agent_login: tuple[Workspace, AgentConnection, User], runners: Runners
) -> None:
    _, login, _ = agent_login

    assert disconnect_agent_connection(login) is True

    assert sorted(runners.asked) == ["default", "runner-b"]
    assert not AgentCredentialCleanup.objects.exists()


def test_a_runner_that_does_not_answer_is_asked_again_alone(
    agent_login: tuple[Workspace, AgentConnection, User], runners: Runners
) -> None:
    _, login, _ = agent_login
    runners.down = {"runner-b"}

    assert disconnect_agent_connection(login) is False

    cleanup = AgentCredentialCleanup.objects.get()
    assert cleanup.removed_from == ["default"]
    assert cleanup.attempts == 1

    runners.down = set()
    runners.asked = []
    assert retry_credential_cleanups() == 1
    assert runners.asked == ["runner-b"]
    assert not AgentCredentialCleanup.objects.exists()


def test_a_down_runner_is_not_asked_but_its_copy_still_counts(
    agent_login: tuple[Workspace, AgentConnection, User], runners: Runners
) -> None:
    _, login, _ = agent_login
    change_runner_state("runner-b", Runner.State.DOWN)

    assert disconnect_agent_connection(login) is False
    assert runners.asked == ["default"]
    assert AgentCredentialCleanup.objects.exists()

    change_runner_state("runner-b", Runner.State.ACTIVE)
    assert retry_credential_cleanups() == 1
    assert not AgentCredentialCleanup.objects.exists()


def test_a_check_runs_on_an_active_runner(
    agent_login: tuple[Workspace, AgentConnection, User],
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
    tmp_path: Any,
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    _, login, _ = agent_login
    sync_configured_runner()
    add_runner("runner-b", "https://runner-b:8002", TOKEN)
    change_runner_state("default", Runner.State.DRAINING)
    used: list[str] = []
    client, _ = agent_runner({"reason": "completed", "succeeded": True})

    def chosen(runner: Runner) -> Any:
        used.append(runner.name)
        return client

    monkeypatch.setattr(agent_logins, "runner_client", chosen)

    check_agent_connection(login)

    assert used == ["runner-b"]


def test_a_check_waits_for_an_active_runner(
    agent_login: tuple[Workspace, AgentConnection, User],
) -> None:
    _, login, _ = agent_login
    sync_configured_runner()
    add_runner("runner-b", "https://runner-b:8002", TOKEN)
    change_runner_state("runner-b", Runner.State.DRAINING)
    change_runner_state("default", Runner.State.DOWN)

    with pytest.raises(AgentConnectionError, match="No runner can check"):
        check_agent_connection(login)


def test_a_check_goes_to_the_login_home_and_sets_it(
    agent_login: tuple[Workspace, AgentConnection, User],
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
    tmp_path: Any,
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    _, login, _ = agent_login
    sync_configured_runner()
    home = add_runner("runner-b", "https://runner-b:8002", TOKEN, max_sessions=1)
    set_login_home(str(login.credential_key), home)
    used: list[str] = []

    def chosen(runner: Runner) -> Any:
        used.append(runner.name)
        # Each check closes its client, so each gets its own.
        client, _ = agent_runner({"reason": "completed", "succeeded": True})
        return client

    monkeypatch.setattr(agent_logins, "runner_client", chosen)

    # default has no session limit, but the login's copy is on runner-b.
    check_agent_connection(login)
    assert used == ["runner-b"]

    # A login with no home gets one from its first check.
    LoginHome.objects.all().delete()
    used.clear()
    check_agent_connection(login)
    assert used == ["default"]
    home_now = login_home(str(login.credential_key))
    assert home_now is not None and home_now.runner.name == "default"
