from datetime import UTC, datetime
from typing import Any

import pytest

from secondshift.config.constants import RunStart
from secondshift.jobs.models import Job
from secondshift.jobs.tests.plans import stored_plan
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run, Task
from secondshift.runs.reporting.notifications import run_report_payload


def finished_run(status: str, summary: str) -> Run:
    repository = Repository(full_name="wild-edge/wild-edge")
    job = Job(
        key="weekly-infra-review",
        name="Weekly Infrastructure Review",
        repository=repository,
    )
    return Run(
        started_by=RunStart.SCHEDULE,
        pk=2,
        job=job,
        task=Task(repository=repository),
        status=status,
        summary=summary,
        commit_sha="a" * 40,
        scheduled_for=datetime(2026, 10, 25, tzinfo=UTC),
        finished_at=None,
        execution_plan=stored_plan(),
    )


@pytest.mark.parametrize(
    ("status", "first_line"),
    [
        (
            Run.Status.SUCCEEDED,
            "✅ Weekly Infrastructure Review succeeded · wild-edge/wild-edge",
        ),
        (
            Run.Status.FAILED,
            "❌ Weekly Infrastructure Review failed · wild-edge/wild-edge",
        ),
        (
            Run.Status.CANCELED,
            "⏹️ Weekly Infrastructure Review was canceled · wild-edge/wild-edge",
        ),
    ],
)
def test_notification_text_puts_status_on_its_own_line(
    settings: Any, status: str, first_line: str
) -> None:
    settings.FRONTEND_URL = "https://secondshift.example/"
    run = finished_run(status, "Couldn't start the sandbox.\n")

    text = run_report_payload(run)["text"]

    assert text.splitlines() == [
        first_line,
        "",
        "Couldn't start the sandbox.",
        "",
        "<https://secondshift.example/runs/2|Open run #2>",
    ]


def test_notification_text_without_a_summary(settings: Any) -> None:
    settings.FRONTEND_URL = "https://secondshift.example"

    text = run_report_payload(finished_run(Run.Status.FAILED, ""))["text"]

    assert text.splitlines()[2] == "No summary."


def test_notifications_name_collected_files_without_their_content(
    settings: Any,
) -> None:
    settings.FRONTEND_URL = "https://secondshift.example/"
    run = finished_run(Run.Status.SUCCEEDED, "Updated the setup guide.")
    run.outputs = {
        "changes": {"file_count": 2, "path": "runs/1/changes.patch"},
        "files": [{"name": "steps.csv", "size_bytes": 40, "path": "runs/1/x"}],
    }

    payload = run_report_payload(run)

    assert payload["outputs"] == {
        "changed_files": 2,
        "saved_files": [{"name": "steps.csv", "size_bytes": 40}],
    }
    assert "2 changed files · 1 saved file" in payload["text"]
    assert "runs/1" not in str(payload)


def test_notifications_without_collected_files_add_no_line(settings: Any) -> None:
    settings.FRONTEND_URL = "https://secondshift.example/"
    run = finished_run(Run.Status.SUCCEEDED, "All good.")
    # Changes that were too large to keep are not counted as delivered files.
    run.outputs = {"changes": {"file_count": 900, "too_large": True}, "files": []}

    payload = run_report_payload(run)

    assert payload["outputs"] == {"changed_files": 0, "saved_files": []}
    assert payload["text"].count("\n\n") == 2


def test_links_to_saved_files_open_their_download(settings: Any) -> None:
    settings.FRONTEND_URL = "https://secondshift.example/"
    run = finished_run(
        Run.Status.SUCCEEDED,
        "See [the list](/outputs/reports/license%20list.csv), "
        "[a|b](/outputs/a.csv) and [missing](/outputs/gone.csv).",
    )
    run.outputs = {
        "files": [
            {"name": "reports/license list.csv", "size_bytes": 9, "path": "x"},
            {"name": "a.csv", "size_bytes": 3, "path": "y"},
        ]
    }

    payload = run_report_payload(run)

    base = "https://secondshift.example/api/runs/2/saved-files"
    assert payload["text"].splitlines()[2] == (
        f"See <{base}/reports/license%20list.csv|the list>, "
        f"<{base}/a.csv|ab> and [missing](/outputs/gone.csv)."
    )
    # The structured summary stays as the agent wrote it.
    assert payload["run"]["summary"].startswith("See [the list](/outputs/")
