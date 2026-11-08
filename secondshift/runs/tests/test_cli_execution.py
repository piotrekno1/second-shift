import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User

from secondshift.connections.agents import AgentConnectionError, sign_in_refused
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
from secondshift.runs.dao import with_model_usage
from secondshift.runs.execution import cli_agent
from secondshift.runs.execution.cli_agent import execute_cli_step
from secondshift.runs.models import AgentLease, Run, RunEvent
from secondshift.runs.serialization import serialize_run, serialize_run_detail

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


def test_cli_run_explains_a_stopped_sandbox_without_blaming_the_login(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    fake_runner(monkeypatch, {"reason": "agent_stopped"})

    result = execute_cli_step(run.pk, "run-1", "Read the skill")

    assert result["succeeded"] is False
    assert result["summary"].startswith("The agent's sandbox stopped")
    assert "login" not in result["summary"]


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


@pytest.mark.parametrize(
    ("backend", "usage", "expected"),
    [
        (
            "claude_code",
            {
                "input_tokens": 20,
                "output_tokens": 3,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 10,
            },
            # Cache reads count as a tenth toward the budget.
            (130, 3, 133, 100, 43),
        ),
        (
            "codex",
            {"input_tokens": 120, "cached_input_tokens": 100, "output_tokens": 3},
            # Codex counts cached input inside input.
            (120, 3, 123, 100, 33),
        ),
        ("codex", {}, (0, 0, None, 0, None)),
        ("claude_code", {"input_tokens": 0, "output_tokens": 0}, (0, 0, 0, 0, 0)),
    ],
)
def test_cli_usage_in_run_responses(
    agent_run: tuple[Run, User],
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    usage: dict[str, int],
    expected: tuple[int, int, int | None, int, int | None],
    django_assert_num_queries: Any,
) -> None:
    run, _ = agent_run
    run.execution_plan["execution"]["backend"] = backend
    run.save(update_fields=["execution_plan"])
    AgentConnection.objects.filter(
        pk=run.job.workspace.default_agent_connection_id
    ).update(backend=backend)
    # Existing unrelated events must not hide the execution result.
    RunEvent.objects.create(
        run=run, message="Earlier event", metadata={"usage": {"input_tokens": 999}}
    )
    fake_runner(monkeypatch, {"report": "Done", "succeeded": True, "usage": usage})
    execute_cli_step(run.pk, "run-1", "Read the skill")
    RunEvent.objects.create(run=run, message="Later event")
    with django_assert_num_queries(1):
        response = serialize_run(
            with_model_usage(Run.objects.select_related("job__repository")).get(
                pk=run.pk
            )
        )
    assert (
        response.model_input_tokens,
        response.model_output_tokens,
        response.model_tokens,
        response.model_cache_read_tokens,
        response.model_budget_tokens,
    ) == expected
    assert response.model_usage_source == "agent"
    assert response.model_cost_usd is None
    assert response.model_requests == 0


@pytest.mark.parametrize(
    ("check", "summary", "refused"),
    [
        ("reconnect_required", "Agent sign-in expired.", True),
        ("completed", "The agent could not finish.", False),
        ("quota_exhausted", "Subscription usage exhausted.", False),
        (None, "Agent sign-in expired.", False),
    ],
)
def test_a_run_that_looks_signed_out_is_confirmed_by_a_check(
    agent_run: tuple[Run, User],
    monkeypatch: pytest.MonkeyPatch,
    check: str | None,
    summary: str,
    refused: bool,
) -> None:
    run, _ = agent_run
    fake_runner(
        monkeypatch, {"report": "", "succeeded": False, "reason": "reconnect_required"}
    )
    checks = Mock()
    if check is None:
        checks.side_effect = AgentConnectionError("busy")
    else:

        def record(connection: AgentConnection) -> AgentConnection:
            connection.last_check = {
                "ok": check != "reconnect_required",
                "reason": check,
            }
            connection.save(update_fields=["last_check"])
            return connection

        checks.side_effect = record
    monkeypatch.setattr(cli_agent, "check_agent_connection", checks)

    result = execute_cli_step(run.pk, "run-1", "Read the skill")

    checks.assert_called_once()
    assert result["summary"].startswith(summary)
    login = AgentConnection.objects.get(
        pk=run.execution_plan["execution"]["connection_id"]
    )
    assert sign_in_refused(login) is refused


def test_other_failures_spend_no_check(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    fake_runner(monkeypatch, {"report": "", "succeeded": False, "reason": "timed_out"})
    checks = Mock()
    monkeypatch.setattr(cli_agent, "check_agent_connection", checks)
    execute_cli_step(run.pk, "run-1", "Read the skill")
    checks.assert_not_called()


def test_cli_run_records_each_subagent_it_ran(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    run.execution_plan["agent"]["subagents"] = 2
    run.save(update_fields=["execution_plan"])
    sent = fake_runner(
        monkeypatch,
        {
            "report": "Done",
            "succeeded": True,
            "reason": "completed",
            "subagents": [
                {"requests": 3, "tool_calls": 2, "finished": 1},
                {"requests": 1, "tool_calls": 0, "finished": 0},
            ],
        },
    )

    execute_cli_step(run.pk, "run-1", "Read the skill")

    assert sent[-1]["json"]["subagents"] == 2
    events = RunEvent.objects.filter(run=run, metadata__has_key="subagent")
    assert [event.message for event in events.order_by("pk")] == [
        "Sub-agent 1 finished after 3 model requests and 2 tool calls.",
        "Sub-agent 2 stopped after 1 model requests and 0 tool calls.",
    ]


def test_cli_run_explains_a_refused_subagent(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    fake_runner(monkeypatch, {"report": "", "reason": "subagent_refused"})

    result = execute_cli_step(run.pk, "run-1", "Read the skill")

    assert result["summary"].startswith("The agent ran a sub-agent this run does")
