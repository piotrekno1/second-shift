from datetime import timedelta

import pytest
from django.utils import timezone

from secondshift.automations import workflows
from secondshift.automations.models import Run


def start_run_in_the_past(run_id: int) -> None:
    Run.objects.filter(pk=run_id).update(
        status=Run.Status.RUNNING,
        started_at=timezone.now() - timedelta(hours=1),
    )


@pytest.fixture
def fake_stages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    stopped: list[str] = []
    monkeypatch.setattr(workflows, "mark_run_started_step", start_run_in_the_past)
    monkeypatch.setattr(
        workflows, "materialize_local_repository_step", lambda run_id: None
    )
    monkeypatch.setattr(
        workflows, "start_runner_session_step", lambda run_id: f"run-{run_id}"
    )
    monkeypatch.setattr(workflows, "stop_runner_session_step", stopped.append)
    monkeypatch.setattr(workflows, "record_run_event_step", lambda *args: None)
    return stopped
