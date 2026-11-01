from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model

from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.orchestration.workflows import create_scheduled_run_step
from secondshift.workspaces.services import create_workspace

TICK = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)


@pytest.fixture
def job(db: object) -> Job:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project", enabled=True
    )
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="triage",
        name="Triage",
        skill_path=".agents/skills/triage/SKILL.md",
        config_hash="hash",
        enabled=True,
        cron="0 8 * * *",
    )


def tick(job: Job, when: datetime = TICK) -> int | None:
    return create_scheduled_run_step.__wrapped__(when, {"job_id": job.pk})


def test_tick_creates_a_run(job: Job) -> None:
    run = Run.objects.get(pk=tick(job))

    assert (run.started_by, run.scheduled_for) == ("schedule", TICK)


def test_replayed_tick_returns_the_same_run(job: Job) -> None:
    first = tick(job)
    Run.objects.filter(pk=first).update(status=Run.Status.RUNNING)

    assert tick(job) == first
    assert Run.objects.count() == 1


@pytest.mark.parametrize("status", [Run.Status.QUEUED, Run.Status.RUNNING])
def test_tick_is_skipped_while_another_run_is_active(job: Job, status: str) -> None:
    active = Run.objects.get(pk=tick(job, TICK - timedelta(days=1)))
    Run.objects.filter(pk=active.pk).update(status=status, started_by="webhook")

    assert tick(job) is None
    assert Run.objects.count() == 1
    note = RunEvent.objects.get(run=active).message
    assert note == (
        "Skipped the scheduled run at 2026-11-02 08:00 UTC "
        "because this run had not finished."
    )
    job.refresh_from_db()
    assert job.next_run_at > TICK


def test_tick_runs_once_the_other_run_finished(job: Job) -> None:
    earlier = tick(job, TICK - timedelta(days=1))
    Run.objects.filter(pk=earlier).update(status=Run.Status.SUCCEEDED)

    assert tick(job) not in (None, earlier)
