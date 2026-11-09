from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from secondshift.config.constants import RunStart
from secondshift.jobs.models import Job
from secondshift.jobs.plan import ExecutionPlan
from secondshift.jobs.tests.plans import stored_plan
from secondshift.repositories.models import Repository
from secondshift.runs.inputs import Payload
from secondshift.runs.models import Run, RunFailure, Task
from secondshift.runs.orchestration import dispatch
from secondshift.runs.retention import prune_runs
from secondshift.runs.services import (
    RunInProgress,
    RunNotReady,
    RunRefused,
    create_run,
    start_manual_run,
    start_run,
)
from secondshift.workspaces.dao import create_workspace


@pytest.fixture
def job(db: object, monkeypatch: pytest.MonkeyPatch) -> Job:
    owner = get_user_model().objects.create_user(username="task-owner")
    workspace = create_workspace(name="Tasks", slug="tasks", owner=owner)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="example/tasks",
        enabled=True,
        config_sha="original-commit",
    )
    monkeypatch.setattr("secondshift.runs.services.enqueue_run", lambda run: None)
    monkeypatch.setattr(
        "secondshift.runs.services.build_execution_plan",
        lambda job: ExecutionPlan.model_validate(stored_plan()),
    )
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        config_hash="original-config",
    )


def test_a_manual_run_records_who_assigned_it(job: Job) -> None:
    owner = get_user_model().objects.get(username="task-owner")
    run = start_manual_run(job, submitted_by=owner)

    assert run.started_by == RunStart.MANUAL
    assert run.task.repository == job.repository
    assert run.task.submitted_by == owner


def test_a_webhook_run_has_no_submitter_and_keeps_its_repeat_key(job: Job) -> None:
    with patch(
        "secondshift.runs.services.store_run_payload",
        return_value=Payload(
            path="payload.json", size_bytes=2, kept_bytes=2, truncated=False
        ),
    ):
        run = start_run(job, RunStart.WEBHOOK, b"{}", "event-1")

    assert run.started_by == RunStart.WEBHOOK
    assert run.task.submitted_by is None
    assert run.input_snapshot["repeat_key"] == "event-1"


def test_active_run_refusal_creates_no_task(job: Job) -> None:
    first = start_manual_run(job)
    with pytest.raises(RunInProgress) as refused:
        start_manual_run(job)

    assert refused.value.run.pk == first.pk
    assert Task.objects.count() == 1


def test_disabled_repository_creates_no_task(job: Job) -> None:
    job.repository.enabled = False
    with pytest.raises(RunRefused):
        start_manual_run(job)
    assert not Task.objects.exists()


def test_readiness_refusal_creates_no_task(
    job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "secondshift.runs.services.build_execution_plan",
        lambda job: ExecutionPlan.model_validate(stored_plan(ready=False)),
    )
    monkeypatch.setattr(
        "secondshift.runs.services.execution_problems", lambda *args: []
    )
    with pytest.raises(RunNotReady):
        start_manual_run(job)
    assert not Task.objects.exists()


def test_run_creation_failure_rolls_back_task(job: Job) -> None:
    first = create_run(job, timezone.now())
    # The real uniqueness constraint fails after the task has been inserted.
    with pytest.raises(IntegrityError):
        create_run(job, first.scheduled_for)
    assert Task.objects.count() == Run.objects.count() == 1


def test_payload_failure_rolls_back_task_and_run(job: Job) -> None:
    with (
        patch(
            "secondshift.runs.services.store_run_payload",
            side_effect=OSError("storage unavailable"),
        ),
        pytest.raises(OSError, match="storage unavailable"),
    ):
        start_run(job, RunStart.WEBHOOK, b"{}")
    assert not Task.objects.exists()
    assert not Run.objects.exists()


def test_retention_keeps_task_until_its_last_attempt_expires(job: Job) -> None:
    first = create_run(job, timezone.now() - timedelta(days=90))
    Run.objects.filter(pk=first.pk).update(
        status=Run.Status.SUCCEEDED, finished_at=timezone.now() - timedelta(days=89)
    )
    second = Run.objects.create(
        started_by=RunStart.SCHEDULE,
        execution_plan=stored_plan(),
        job=job,
        task_id=first.task_id,
        scheduled_for=timezone.now(),
    )

    assert prune_runs(days=30) == 1
    assert Task.objects.filter(pk=first.task_id).exists()
    Run.objects.filter(pk=second.pk).update(
        status=Run.Status.FAILED, finished_at=timezone.now() - timedelta(days=89)
    )
    assert prune_runs(days=30) == 1
    assert not Task.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_upgrading_from_the_last_release_keeps_every_run(job: Job) -> None:
    before = [("runs", "0012_alter_rundelivery_status")]
    after = [("runs", "0013_release_0_1_0b11")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    try:
        old_apps = executor.loader.project_state(before).apps
        old_run = old_apps.get_model("runs", "Run")
        old_task = old_apps.get_model("runs", "Task")
        now = timezone.now()
        fields = {"commit_sha": "a" * 40, "execution_plan": stored_plan()}
        without_task = old_run.objects.create(
            job_id=job.pk, started_by="schedule", scheduled_for=now, **fields
        )
        job_task = old_task.objects.create(
            job_id=job.pk, commit_sha="a" * 40, scheduled_for=now
        )
        not_queued = old_run.objects.create(
            job_id=job.pk,
            task=job_task,
            started_by="manual",
            scheduled_for=now + timedelta(seconds=1),
            status="failed",
            summary="The durable workflow could not be enqueued.",
            **fields,
        )
        submitted = old_task.objects.create(
            repository_id=job.repository_id,
            submission_key="key",
            commit_sha="a" * 40,
            scheduled_for=now,
            input_snapshot={"submission_fingerprint": "f" * 64},
        )
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        task_model = apps.get_model("runs", "Task")
        run_model = apps.get_model("runs", "Run")

        assert run_model.objects.get(pk=without_task.pk).task.repository_id == (
            job.repository_id
        )
        assert task_model.objects.get(pk=job_task.pk).repository_id == (
            job.repository_id
        )
        assert task_model.objects.get(pk=submitted.pk).fingerprint == "f" * 64
        assert run_model.objects.get(pk=not_queued.pk).failure == "not_queued"
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_a_job_run_the_queue_refuses_is_marked_failed(
    job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = create_run(job, timezone.now())
    client = Mock()
    client.enqueue.side_effect = ConnectionError("queue is down")
    monkeypatch.setattr(dispatch, "new_dbos_client", lambda: client)

    with pytest.raises(dispatch.RunEnqueueFailed):
        dispatch.enqueue_run(run)

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    assert run.failure == RunFailure.NOT_QUEUED


def test_a_job_run_the_queue_accepted_stays_queued_when_saving_fails(
    job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = create_run(job, timezone.now())
    client = Mock()
    client.enqueue.return_value.get_workflow_id.return_value = "workflow-1"
    monkeypatch.setattr(dispatch, "new_dbos_client", lambda: client)
    save = Run.save

    def save_fails_for_the_workflow_id(self: Run, **kwargs: object) -> None:
        if kwargs.get("update_fields") == ["dbos_workflow_id"]:
            raise DatabaseError("database went away")
        save(self, **kwargs)

    monkeypatch.setattr(Run, "save", save_fails_for_the_workflow_id)

    with pytest.raises(DatabaseError):
        dispatch.enqueue_run(run)

    assert Run.objects.get(pk=run.pk).status == Run.Status.QUEUED
