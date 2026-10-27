from types import SimpleNamespace

import pytest
import requests
from pydantic_ai.exceptions import ModelHTTPError

from secondshift.repositories.github_app import GitHubAppError
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.runs.models import Run
from secondshift.runs.orchestration import workflows
from secondshift.runs.reporting.failures import (
    FailureView,
    describe_failure,
    failure_metadata,
    failure_summary,
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

    assert failure_summary("sandbox", error) == (
        "Couldn't start the sandbox: Runner answered HTTP 500."
    )
    assert failure_metadata("sandbox", error) == {
        "stage": "sandbox",
        "error_type": "RunnerProtocolError",
        "attempts": 2,
    }


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (
            requests.ConnectionError("http://runner:8002 refused"),
            "could not be reached",
        ),
        (requests.ReadTimeout("read timed out"), "did not answer in time"),
        (KeyError("secret-looking-value"), "Unexpected KeyError."),
    ],
)
def test_summary_hides_messages_it_did_not_write(error: Exception, detail: str) -> None:
    summary = failure_summary("skill", error)

    assert summary.startswith("The skill run failed: ")
    assert detail in summary
    assert "secret-looking-value" not in summary
    assert "runner:8002" not in summary


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

    assert failure_summary("skill", error) == (
        f"The skill run failed: The model provider answered {expected}"
    )


def test_summary_is_one_bounded_line() -> None:
    summary = failure_summary(
        "checkout", RuntimeError("line one\nline two " + "x" * 500)
    )

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

    monkeypatch.setattr(
        workflows, "materialize_local_repository_step", lambda run_id: None
    )
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


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (
            "The skill run failed: The model provider answered HTTP 401: User not found.",
            FailureView("Model provider", "HTTP 401 · User not found."),
        ),
        (
            "Couldn't start the sandbox: Runner answered HTTP 500.",
            FailureView("Sandbox", "Runner answered HTTP 500."),
        ),
        (
            "The skill run failed: Unexpected KeyError.",
            FailureView("Skill", "Unexpected KeyError."),
        ),
        (
            "The durable workflow failed unexpectedly (DBOSMaxStepRetriesExceeded).",
            FailureView("Workflow", "Unexpected DBOSMaxStepRetriesExceeded."),
        ),
        ("Timed out", FailureView("Run", "Timed out")),
        ("", FailureView("Run", "No reason was recorded.")),
    ],
)
def test_describe_failure_splits_label_and_reason(
    summary: str, expected: FailureView
) -> None:
    assert describe_failure(summary) == expected


def test_describe_failure_reads_what_failure_summary_writes() -> None:
    error = RetriesExceeded([RunnerProtocolError("Runner answered HTTP 500.")])

    view = describe_failure(failure_summary("sandbox", error))

    assert view == FailureView("Sandbox", "Runner answered HTTP 500.")


def test_checkout_explains_github_access_failure() -> None:
    assert failure_summary(
        "checkout", GitHubAppError("The GitHub installation is disconnected.")
    ) == ("Couldn't check out the repository: The GitHub installation is disconnected.")


MODEL_PLAN = {"model": {"connection_id": 2, "connection_name": "OpenRouter"}}


@pytest.mark.parametrize(
    ("detail", "next_step"),
    [
        (
            "HTTP 401: API key expired.",
            "The provider refused the API key of OpenRouter. "
            "Update the key, then run again.",
        ),
        ("HTTP 429.", "The provider is limiting requests. Wait, then run again."),
        ("HTTP 503.", "The provider had a problem. Run again later."),
    ],
)
def test_model_provider_failure_names_the_connection_to_fix(
    detail: str, next_step: str
) -> None:
    run = Run(
        status=Run.Status.FAILED,
        summary=f"The skill run failed: The model provider answered {detail}",
        execution_plan=MODEL_PLAN,
    )

    failure = run_failure(run)

    assert failure is not None
    assert failure.connection_id == 2
    assert failure.next_step == next_step


def test_other_failures_point_at_no_connection() -> None:
    run = Run(
        status=Run.Status.FAILED,
        summary="Couldn't start the sandbox: Runner answered HTTP 500.",
        execution_plan=MODEL_PLAN,
    )

    failure = run_failure(run)

    assert failure is not None
    assert failure.connection_id is None
    assert failure.next_step == ""
