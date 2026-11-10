from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from secondshift.config.constants import RunStart
from secondshift.connections.errors import ExternalToolError
from secondshift.connections.mcp.base import MCPRequestError
from secondshift.github.errors import GitHubError
from secondshift.jobs.tests.plans import CAPABILITY, MODEL, stored_plan
from secondshift.jobs.tool_rules import ToolRuleViolation
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.runs.migration_helpers import failure_from_summary_v1
from secondshift.runs.models import Run, RunFailure
from secondshift.runs.orchestration import workflows
from secondshift.runs.reporting.failures import (
    failure_metadata,
    stage_failure,
)
from secondshift.runs.serialization import run_failure
from secondshift.runs.tests.test_workflows import execution_run


class RetriesExceeded(Exception):
    """Shaped like DBOS's error for a step that ran out of retries."""

    def __init__(self, errors: list[Exception]) -> None:
        super().__init__("Step has exceeded its maximum of 3 retries")
        self.errors = errors


def test_summary_names_the_stage_and_the_first_attempts_cause() -> None:
    error = RetriesExceeded(
        [
            RunnerProtocolError("Runner answered HTTP 500."),
            RunnerProtocolError("The previous session outcome is not reusable."),
        ]
    )

    assert stage_failure(RunFailure.SANDBOX, error).summary == (
        "Couldn't start the sandbox: Runner answered HTTP 500."
    )
    assert failure_metadata("sandbox", error) == {
        "stage": "sandbox",
        "error_type": "RunnerProtocolError",
        "attempts": 2,
    }


def test_summary_hides_messages_it_did_not_write() -> None:
    summary = stage_failure(RunFailure.SKILL, KeyError("secret-looking-value")).summary

    assert summary == "The skill run failed: Unexpected KeyError."


def test_summary_shows_tool_errors_secondshift_wrote() -> None:
    error = ExternalToolError("Access to this tool is no longer approved.")

    assert stage_failure(RunFailure.SKILL, error).summary == (
        "The skill run failed: Access to this tool is no longer approved."
    )


def test_summary_hides_tool_errors_that_may_quote_the_service() -> None:
    error = MCPRequestError("remote text: secret-looking-value")

    assert stage_failure(RunFailure.SKILL, error).summary == (
        "The skill run failed: Unexpected MCPRequestError."
    )


def test_a_broken_tool_restriction_is_not_blamed_on_the_skill() -> None:
    error = ToolRuleViolation(
        "mcp.slack.post_message: `channel` must be one of the approved values."
    )
    stopped = stage_failure(RunFailure.SKILL, error)
    run = Run(
        status=Run.Status.FAILED,
        summary=stopped.summary,
        failure=stopped.kind,
        failure_reason=stopped.reason,
    )

    failure = run_failure(run)

    assert failure is not None
    assert failure.label == "Tool restriction"
    assert failure.reason == (
        "mcp.slack.post_message: `channel` must be one of the approved values."
    )
    assert "access review" in failure.next_step


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"message": "User not found.", "code": 401}, "HTTP 401: User not found."),
        ({"error": {"message": "Bad key", "type": "auth"}}, "HTTP 401: Bad key"),
        (None, "HTTP 401."),
    ],
)
def test_summary_reads_model_provider_errors(body: object, expected: str) -> None:
    error = ModelHTTPError(401, "anthropic/claude-haiku-5.5", body)

    assert stage_failure(RunFailure.SKILL, error).summary == (
        f"The skill run failed: The model provider answered {expected}"
    )


def test_summary_is_one_bounded_line() -> None:
    summary = stage_failure(
        RunFailure.CHECKOUT, RuntimeError("line one\nline two " + "x" * 500)
    ).summary

    assert "\n" not in summary
    assert len(summary) < 400
    assert summary.endswith("…")


@pytest.mark.django_db
def test_failed_run_records_stage_events_and_a_clear_summary(db, monkeypatch) -> None:
    run = execution_run(db)
    run.input_snapshot = {"skill_path": ".agents/skills/review/SKILL.md"}
    run.save(update_fields=["input_snapshot"])

    def fail_to_start(run_id: int) -> str:
        raise RetriesExceeded([RunnerProtocolError("Runner answered HTTP 500.")])

    monkeypatch.setattr(workflows, "prepare_source_step", lambda run_id: None)
    monkeypatch.setattr(workflows, "start_runner_session_step", fail_to_start)
    # Outside a real workflow DBOS has no workflow id to record.
    monkeypatch.setattr(workflows, "DBOS", SimpleNamespace(workflow_id="wf-test"))

    with pytest.raises(RetriesExceeded):
        workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    assert run.summary == "Couldn't start the sandbox: Runner answered HTTP 500."
    events = list(run.events.values_list("level", "message", "metadata"))
    assert [message for _, message, _ in events] == ["Run started.", run.summary]
    level, _, metadata = events[-1]
    assert level == "error"
    assert metadata == {
        "stage": "sandbox",
        "error_type": "RunnerProtocolError",
        "attempts": 1,
        "workflow_status": "ERROR",
    }


@pytest.mark.django_db
def test_a_run_that_lost_its_access_fails_before_any_work(db, monkeypatch) -> None:
    run = execution_run(db)
    run.job.repository.enabled = True
    run.job.repository.save(update_fields=["enabled"])
    run.execution_plan = stored_plan(
        ready=False,
        capabilities=[CAPABILITY | {"reasons": ["grant_denied"]}],
    )
    run.save(update_fields=["execution_plan"])
    prepared: list[int] = []
    delivered: list[int] = []
    monkeypatch.setattr(workflows, "prepare_source_step", prepared.append)
    monkeypatch.setattr(workflows, "deliver", delivered.append)
    monkeypatch.setattr(workflows, "DBOS", SimpleNamespace(workflow_id="wf-test"))

    with pytest.raises(workflows.RunNotReadyError):
        workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    assert (
        run.summary == "The run could not start: observability was denied by an owner."
    )
    assert run.started_at is None
    assert prepared == []
    # A failed scheduled run still notifies.
    assert delivered == [run.pk]
    assert list(run.events.values_list("message", flat=True)) == [run.summary]
    failure = run_failure(run)
    assert failure is not None
    assert (failure.label, failure.reason) == (
        "Start",
        "observability was denied by an owner.",
    )


@pytest.mark.django_db
def test_success_event_does_not_repeat_the_report(db) -> None:
    run = execution_run(db)
    Run.objects.filter(pk=run.pk).update(status=Run.Status.RUNNING)

    workflows.finish_run_step(
        run.pk,
        {"succeeded": True, "exit_code": 0, "summary": "**Long report**"},
    )

    run.refresh_from_db()
    assert run.summary == "**Long report**"
    assert list(run.events.values_list("message", flat=True)) == [
        "Run succeeded. The report is stored as the run's output."
    ]


def test_model_provider_and_tool_rule_errors_get_their_own_kind() -> None:
    provider = stage_failure(
        RunFailure.SKILL,
        ModelHTTPError(401, "m", {"message": "User not found."}),
    )
    rule = stage_failure(RunFailure.SKILL, ToolRuleViolation("`channel` is fixed."))
    runner = stage_failure(
        RunFailure.SANDBOX,
        RetriesExceeded([RunnerProtocolError("Runner answered HTTP 500.")]),
    )

    assert (provider.kind, provider.reason) == (
        RunFailure.MODEL_PROVIDER,
        "HTTP 401 · User not found.",
    )
    assert (rule.kind, rule.reason) == (RunFailure.TOOL_RULE, "`channel` is fixed.")
    assert (runner.kind, runner.reason) == (
        RunFailure.SANDBOX,
        "Runner answered HTTP 500.",
    )


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (
            "The skill run failed: The model provider answered HTTP 401: User not found.",
            ("model_provider", "HTTP 401 · User not found."),
        ),
        (
            "Couldn't start the sandbox: Runner answered HTTP 500.",
            ("sandbox", "Runner answered HTTP 500."),
        ),
        (
            "Timed out: Stopped after 5 minutes.",
            ("timeout", "Stopped after 5 minutes."),
        ),
        (
            "The durable workflow could not be enqueued.",
            ("not_queued", "The durable workflow could not be enqueued."),
        ),
        (
            "The durable workflow failed unexpectedly (KeyError).",
            ("other", "Unexpected KeyError."),
        ),
        ("Timed out", ("other", "Timed out")),
    ],
)
def test_migration_reads_the_failure_from_an_older_summary(
    summary: str, expected: tuple[str, str]
) -> None:
    assert failure_from_summary_v1(summary) == expected


def test_checkout_explains_github_access_failure() -> None:
    assert stage_failure(
        RunFailure.CHECKOUT, GitHubError("The GitHub installation is disconnected.")
    ).summary == (
        "Couldn't check out the repository: The GitHub installation is disconnected."
    )


MODEL_PLAN = stored_plan(
    model=MODEL | {"connection_id": 2, "connection_name": "OpenRouter"}
)


@pytest.mark.parametrize(
    ("detail", "next_step"),
    [
        (
            "HTTP 401 · API key expired.",
            "The API key for OpenRouter was refused. Update the key, then run again.",
        ),
        ("HTTP 429.", "The provider is limiting requests. Wait, then run again."),
        ("HTTP 503.", "The provider had a problem. Run again later."),
    ],
)
def test_model_provider_failure_names_the_connection_to_fix(
    detail: str, next_step: str
) -> None:
    run = Run(
        started_by=RunStart.SCHEDULE,
        status=Run.Status.FAILED,
        failure=RunFailure.MODEL_PROVIDER,
        failure_reason=detail,
        execution_plan=MODEL_PLAN,
    )

    failure = run_failure(run)

    assert failure is not None
    assert failure.connection_id == 2
    assert failure.next_step == next_step


def test_other_failures_point_at_no_connection() -> None:
    run = Run(
        started_by=RunStart.SCHEDULE,
        status=Run.Status.FAILED,
        failure=RunFailure.SANDBOX,
        failure_reason="Runner answered HTTP 500.",
        execution_plan=MODEL_PLAN,
    )

    failure = run_failure(run)

    assert failure is not None
    assert failure.connection_id is None
    assert failure.next_step == ""
