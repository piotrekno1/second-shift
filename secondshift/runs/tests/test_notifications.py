from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from secondshift.runs.models import Run
from secondshift.runs.reporting.notifications import run_report_payload


def finished_run(status: str, summary: str) -> Any:
    repository = SimpleNamespace(full_name="wild-edge/wild-edge")
    job = SimpleNamespace(
        key="weekly-infra-review",
        name="Weekly Infrastructure Review",
        repository=repository,
    )
    return SimpleNamespace(
        pk=2,
        job=job,
        status=status,
        summary=summary,
        commit_sha="a" * 40,
        scheduled_for=datetime(2026, 10, 25, tzinfo=UTC),
        finished_at=None,
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
