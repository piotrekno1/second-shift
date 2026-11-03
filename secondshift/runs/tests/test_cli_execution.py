import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User

from secondshift.connections.models import AgentConnection
from secondshift.connections.tests.test_agent_connections import (
    TOKEN,
)
from secondshift.connections.tests.test_agent_connections import (
    agent_login as agent_login_fixture,
)
from secondshift.jobs.tests.test_execution_defaults import (
    agent_run as agent_run_fixture,
)
from secondshift.runner.agent_credentials import (
    decrypt_agent_auth,
    provisioning_public_key,
)
from secondshift.runs.execution import cli_agent
from secondshift.runs.execution.cli_agent import execute_cli_step
from secondshift.runs.models import AgentLease, Run, RunEvent
from secondshift.runs.serialization import serialize_run_detail

agent_login = agent_login_fixture
agent_run = agent_run_fixture
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def memory_artifacts(settings: Any, tmp_path: Path) -> None:
    settings.STORAGES = {
        **settings.STORAGES,
        "run_artifacts": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"


def fake_runner(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]
) -> list[dict[str, Any]]:
    """Answer the provisioning key and agent requests, and record the bodies."""
    sent: list[dict[str, Any]] = []
    runner = Mock()

    def send(method: str, path: str, **kwargs: Any) -> str:
        sent.append({"method": method, "path": path, **kwargs})
        return path

    def payload(path: str) -> dict[str, Any]:
        if path == "/v1/agent-provisioning-key":
            return {"public_key": provisioning_public_key()}
        return result

    runner.send.side_effect = send
    runner.response_payload.side_effect = payload
    monkeypatch.setattr(cli_agent, "get_runner_client", lambda: runner)
    return sent


def test_cli_run_sends_sealed_login_and_stores_the_report(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    sent = fake_runner(
        monkeypatch,
        {"report": "Done", "succeeded": True, "reason": "completed", "usage": {}},
    )

    result = execute_cli_step(run.pk, "run-1", "Read the skill")

    assert result["succeeded"] is True
    assert result["summary"] == "Done"
    request = sent[-1]
    assert request["path"] == "/v1/sessions/run-1/agent"
    assert TOKEN not in json.dumps(request)
    assert decrypt_agent_auth(request["json"]["auth"]) == TOKEN
    assert AgentLease.objects.get(run=run).active is False
    assert RunEvent.objects.filter(run=run, metadata__backend="claude_code").exists()


def test_cli_run_explains_an_exhausted_subscription(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    # Even if a runner sent error text as a report, it is never stored.
    fake_runner(
        monkeypatch, {"report": "Usage limit reached", "reason": "quota_exhausted"}
    )

    result = execute_cli_step(run.pk, "run-1", "Read the skill")

    assert result["succeeded"] is False
    assert result["output"] is None
    assert result["summary"].startswith("Subscription usage exhausted.")


def test_cli_run_is_never_repeated(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    fake_runner(monkeypatch, {"report": "Done", "succeeded": True})
    execute_cli_step(run.pk, "run-1", "Read the skill")
    # A worker that stopped mid-run retries the step. The agent never runs twice.
    with pytest.raises(RuntimeError, match="interrupted"):
        execute_cli_step(run.pk, "run-1", "Read the skill")


def test_cli_run_explains_a_login_lost_before_start(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    sent = fake_runner(monkeypatch, {"report": "Done", "succeeded": True})
    AgentConnection.objects.filter(
        pk=run.job.workspace.default_agent_connection_id
    ).update(connected=False)
    with pytest.raises(RuntimeError, match="Reconnect"):
        execute_cli_step(run.pk, "run-1", "Read the skill")
    assert not AgentLease.objects.filter(run=run).exists()
    assert sent == []


def test_run_detail_names_the_frozen_agent(agent_run: tuple[Run, User]) -> None:
    run, _ = agent_run
    assert (
        serialize_run_detail(run).execution_agent
        == run.execution_plan["execution"]["label"]
    )


def test_subscription_runs_show_no_model_call_count(
    agent_run: tuple[Run, User],
) -> None:
    run, _ = agent_run
    assert serialize_run_detail(run).max_turns is None
