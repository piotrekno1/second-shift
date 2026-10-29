import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.utils import timezone

from secondshift.connections.models import Connection
from secondshift.core.formatting import duration_label
from secondshift.gateway.models import RunToken
from secondshift.gateway.tokens import issue_run_token
from secondshift.runs.execution import agent
from secondshift.runs.execution.agent import RUN_MODEL_ID
from secondshift.runs.execution.deadlines import RunTimedOut, run_deadline
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps, run_command
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.orchestration import workflows
from secondshift.runs.reporting.failures import describe_failure
from secondshift.runs.tests.test_execution import tool_context
from secondshift.runs.tests.test_workflows import execution_run

PAST = timezone.now() - timedelta(seconds=1)


def test_deadline_counts_from_the_start(settings: Any) -> None:
    settings.RUN_TIMEOUT_SECONDS = 900
    started = timezone.now() - timedelta(hours=1)

    assert run_deadline(Run(started_at=started)) == started + timedelta(minutes=15)


def test_the_deadline_uses_the_limit_frozen_into_the_plan(settings: Any) -> None:
    settings.RUN_TIMEOUT_SECONDS = 900
    started = timezone.now()
    run = Run(
        started_at=started,
        execution_plan={"agent": {"limits": {"timeout_seconds": 3600}}},
    )

    assert run_deadline(run) == started + timedelta(hours=1)


def test_duration_label_reads_naturally() -> None:
    assert duration_label(60) == "1 minute"
    assert duration_label(900) == "15 minutes"
    assert duration_label(90) == "90 seconds"


def test_tool_calls_stop_after_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        "secondshift.runs.execution.workspace_tools.execute_runner_command_step",
        lambda *args: calls.append(args),
    )
    deps = WorkspaceToolDeps(
        run_id=7, session_id="run-7", max_output_chars=80, deadline=PAST
    )

    with pytest.raises(RunTimedOut, match="time limit"):
        run_command(tool_context(deps), ["ls"], ".", 30)

    assert calls == []


def test_model_requests_stop_after_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued: list[Any] = []
    monkeypatch.setattr(agent, "run_model", lambda run_id: issued.append(run_id))
    context = SimpleNamespace(deps=SimpleNamespace(run_id=1, deadline=PAST))

    with pytest.raises(RunTimedOut):
        asyncio.run(agent.resolve_run_model(context, RUN_MODEL_ID))

    assert issued == []


@pytest.mark.django_db
def test_model_access_ends_at_the_deadline(db: object, settings: Any) -> None:
    settings.RUN_TIMEOUT_SECONDS = 900
    run = execution_run(db)
    run.started_at = timezone.now() - timedelta(minutes=5)
    run.save(update_fields=["started_at"])
    model = Connection.objects.create(
        workspace=run.job.workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Model",
        status=Connection.Status.CONNECTED,
        is_default=True,
    )

    issue_run_token(run, model)

    token = RunToken.objects.get(run=run)
    assert token.expires_at == run.started_at + timedelta(minutes=15)


@pytest.mark.django_db
def test_a_run_past_its_limit_is_marked_timed_out(
    db: object, settings: Any, monkeypatch: pytest.MonkeyPatch, fake_stages: list[str]
) -> None:
    settings.RUN_TIMEOUT_SECONDS = 900
    run = execution_run(db)

    def hit_deadline(run: Run, session_id: str) -> None:
        # Retries wrap the error, so the workflow must not rely on its type.
        raise RuntimeError("wrapped") from RunTimedOut("late")

    monkeypatch.setattr(workflows, "execute_agent_run", hit_deadline)

    with pytest.raises(RuntimeError):
        workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    assert describe_failure(run.summary).label == "Timed out"
    assert "Stopped after 15 minutes" in run.summary
    assert fake_stages == [f"run-{run.pk}"]
    event = RunEvent.objects.filter(run=run, level="error").get()
    assert event.metadata["timed_out"] is True


@pytest.mark.django_db
def test_a_failure_within_the_limit_keeps_its_cause(
    db: object, settings: Any, monkeypatch: pytest.MonkeyPatch, fake_stages: list[str]
) -> None:
    settings.RUN_TIMEOUT_SECONDS = 4 * 3600
    run = execution_run(db)

    def fail(run: Run, session_id: str) -> None:
        raise RuntimeError("The agent returned an empty report.")

    monkeypatch.setattr(workflows, "execute_agent_run", fail)

    with pytest.raises(RuntimeError):
        workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert describe_failure(run.summary).label == "Skill"
    assert "empty report" in run.summary
