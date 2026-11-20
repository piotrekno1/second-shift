from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run, RunEvent, Task
from secondshift.runs.orchestration import dispatch, workflows
from secondshift.runs.orchestration.reconciliation import reconcile_terminal_workflows
from secondshift.runs.orchestration.workflows import create_scheduled_run_step
from secondshift.workspaces.dao import create_workspace

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
    assert run.task.submitted_by is None


def test_interrupted_tick_keeps_its_workflow(
    job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = "scheduled-workflow"
    monkeypatch.setattr(workflows, "DBOS", SimpleNamespace(workflow_id=workflow_id))
    queue = Mock(return_value="duplicate-workflow")
    monkeypatch.setattr(dispatch, "queue_workflow", queue)

    run = Run.objects.get(pk=tick(job))
    assert run.status == Run.Status.QUEUED
    grace = timedelta(seconds=dispatch.UNQUEUED_GRACE_SECONDS + 1)
    Run.objects.filter(pk=run.pk).update(created_at=timezone.now() - grace)

    assert dispatch.recover_unqueued_runs() == 0
    queue.assert_not_called()
    assert run.dbos_workflow_id == workflow_id
    assert tick(job) == run.pk
    assert Run.objects.count() == 1

    client = Mock()
    client.list_workflows.return_value = [
        SimpleNamespace(workflow_id=workflow_id, status="ERROR")
    ]
    assert reconcile_terminal_workflows(client) == 1
    assert client.list_workflows.call_args.kwargs["workflow_ids"] == [workflow_id]
    run.refresh_from_db()
    assert run.status == Run.Status.FAILED


def test_queued_tick_is_skipped_after_cron_is_removed(job: Job) -> None:
    job.cron = ""
    job.save(update_fields=["cron"])

    assert tick(job) is None
    assert not Run.objects.exists()
    assert not Task.objects.exists()


def test_replayed_tick_returns_the_same_run(job: Job) -> None:
    first = tick(job)
    Run.objects.filter(pk=first).update(status=Run.Status.RUNNING)

    assert tick(job) == first
    assert Run.objects.count() == 1
    assert Task.objects.count() == 1


@pytest.mark.parametrize("status", [Run.Status.QUEUED, Run.Status.RUNNING])
def test_tick_is_skipped_while_another_run_is_active(job: Job, status: str) -> None:
    active = Run.objects.get(pk=tick(job, TICK - timedelta(days=1)))
    Run.objects.filter(pk=active.pk).update(status=status, started_by="webhook")

    assert tick(job) is None
    assert Run.objects.count() == 1
    assert Task.objects.count() == 1
    note = RunEvent.objects.get(run=active).message
    assert note == (
        "Skipped the scheduled run at 2026-11-02 08:00 UTC "
        "because this run had not finished."
    )
    job.refresh_from_db()
    assert job.next_run_at > TICK


@pytest.mark.parametrize("finish_before_replay", [False, True])
def test_replayed_overlap_skip_stays_skipped(
    job: Job, finish_before_replay: bool
) -> None:
    active = Run.objects.get(pk=tick(job, TICK - timedelta(days=1)))
    assert tick(job) is None
    if finish_before_replay:
        Run.objects.filter(pk=active.pk).update(status=Run.Status.SUCCEEDED)

    assert tick(job) is None
    assert Run.objects.count() == 1
    assert RunEvent.objects.filter(run=active).count() == 1

    if finish_before_replay:
        assert tick(job, TICK + timedelta(days=1)) is not None


def test_tick_runs_once_the_other_run_finished(job: Job) -> None:
    earlier = tick(job, TICK - timedelta(days=1))
    Run.objects.filter(pk=earlier).update(status=Run.Status.SUCCEEDED)

    assert tick(job) not in (None, earlier)
