from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from secondshift.config.constants import RunStart
from secondshift.jobs.models import Job
from secondshift.jobs.tests.plans import stored_plan
from secondshift.repositories.models import Repository
from secondshift.runs.dao import (
    active_runs,
    active_tasks,
    latest_active_run,
    run_for_repeat_key,
    started_run_count,
)
from secondshift.runs.models import RUN_ENQUEUE_FAILED, Run, Task
from secondshift.workspaces.dao import create_workspace


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
    )


def make_run(job: Job | None, status: str, **fields: object) -> Run:
    return Run.objects.create(
        job=job,
        status=status,
        scheduled_for=timezone.now(),
        execution_plan=stored_plan(),
        **fields,
    )


def test_active_runs_are_queued_or_running(job: Job) -> None:
    queued = make_run(job, Run.Status.QUEUED)
    running = make_run(job, Run.Status.RUNNING)
    make_run(job, Run.Status.SUCCEEDED)
    make_run(job, Run.Status.FAILED)

    assert set(active_runs()) == {queued, running}


def test_latest_active_run_is_the_newest_unfinished_one(job: Job) -> None:
    assert latest_active_run(job) is None
    make_run(job, Run.Status.QUEUED)
    newest = make_run(job, Run.Status.RUNNING)
    make_run(job, Run.Status.SUCCEEDED)

    assert latest_active_run(job) == newest


def test_active_tasks_are_unfinished_ad_hoc_runs_in_the_repository(job: Job) -> None:
    task = Task.objects.create(
        repository=job.repository, commit_sha="abc", scheduled_for=timezone.now()
    )
    ad_hoc = make_run(None, Run.Status.QUEUED, task=task)
    make_run(None, Run.Status.SUCCEEDED, task=task)
    make_run(job, Run.Status.QUEUED)

    assert list(active_tasks(job.repository_id)) == [ad_hoc]


def test_run_for_repeat_key_skips_old_and_unqueued_runs(job: Job) -> None:
    since = timezone.now() - timedelta(minutes=10)
    snapshot = {"repeat_key": "event-1"}
    make_run(
        job,
        Run.Status.SUCCEEDED,
        input_snapshot=snapshot,
        finished_at=since - timedelta(minutes=1),
    )
    make_run(
        job, Run.Status.FAILED, input_snapshot=snapshot, summary=RUN_ENQUEUE_FAILED
    )
    assert run_for_repeat_key(job, "event-1", since) is None

    recent = make_run(
        job, Run.Status.SUCCEEDED, input_snapshot=snapshot, finished_at=timezone.now()
    )
    active = make_run(job, Run.Status.QUEUED, input_snapshot=snapshot)
    assert run_for_repeat_key(job, "event-1", since) == active
    active.delete()
    assert run_for_repeat_key(job, "event-1", since) == recent
    assert run_for_repeat_key(job, "event-2", since) is None


def test_started_run_count_ignores_unqueued_and_other_sources(job: Job) -> None:
    since = timezone.now() - timedelta(hours=1)
    make_run(job, Run.Status.SUCCEEDED, started_by=RunStart.WEBHOOK)
    make_run(
        job,
        Run.Status.FAILED,
        started_by=RunStart.WEBHOOK,
        summary=RUN_ENQUEUE_FAILED,
    )
    make_run(job, Run.Status.SUCCEEDED, started_by=RunStart.SCHEDULE)
    old = make_run(job, Run.Status.SUCCEEDED, started_by=RunStart.WEBHOOK)
    Run.objects.filter(pk=old.pk).update(created_at=since - timedelta(minutes=1))

    assert started_run_count(job, RunStart.WEBHOOK, since) == 1
