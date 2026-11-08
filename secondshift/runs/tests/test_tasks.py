from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from secondshift.config.constants import RunStart, TaskOrigin
from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run, Task
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
        "secondshift.runs.services.build_execution_plan", lambda job: {"ready": True}
    )
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        config_hash="original-config",
    )


def test_task_freezes_assignment_independently_of_job_and_attempt(job: Job) -> None:
    owner = get_user_model().objects.get(username="task-owner")
    run = start_manual_run(job, submitted_by=owner)
    task = Task.objects.get(pk=run.task_id)
    snapshot = deepcopy(run.input_snapshot)

    assert task.job == job
    assert task.submitted_by == owner
    assert task.origin == TaskOrigin.MANUAL
    assert task.scheduled_for == run.scheduled_for
    job.skill_path = "changed/SKILL.md"
    job.save()
    job.repository.config_sha = "new-commit"
    job.repository.save()
    run.input_snapshot["attempt-only"] = True
    run.save()

    task.refresh_from_db()
    assert task.input_snapshot == snapshot
    assert task.commit_sha == "original-commit"


def test_webhook_assignment_includes_payload_and_repeat_key(job: Job) -> None:
    with patch(
        "secondshift.runs.services.store_run_payload",
        return_value={"path": "payload.json", "size_bytes": 2},
    ):
        run = start_run(job, RunStart.WEBHOOK, b"{}", "event-1")

    task = Task.objects.get(pk=run.task_id)
    assert task.origin == TaskOrigin.WEBHOOK
    assert task.submitted_by is None
    assert task.input_snapshot == run.input_snapshot
    assert task.input_snapshot["repeat_key"] == "event-1"


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
        "secondshift.runs.services.build_execution_plan", lambda job: {"ready": False}
    )
    monkeypatch.setattr("secondshift.runs.services.plan_problems", lambda *args: [])
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
        job=job, task_id=first.task_id, scheduled_for=timezone.now()
    )

    assert prune_runs(days=30) == 1
    assert Task.objects.filter(pk=first.task_id).exists()
    Run.objects.filter(pk=second.pk).update(
        status=Run.Status.FAILED, finished_at=timezone.now() - timedelta(days=89)
    )
    assert prune_runs(days=30) == 1
    assert not Task.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_migration_backfills_recorded_inputs_without_reading_current_jobs(
    job: Job,
) -> None:
    before = [("runs", "0003_agentlease")]
    after = [("runs", "0005_backfill_tasks")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    try:
        old_apps = executor.loader.project_state(before).apps
        old_run = old_apps.get_model("runs", "Run")
        created = timezone.now() - timedelta(days=45)
        snapshot = {"skill_path": "old/SKILL.md", "payload": {"path": "old/body"}}
        historical = [
            old_run.objects.create(
                job_id=job.pk,
                started_by=origin,
                commit_sha="historical-commit",
                input_snapshot=snapshot,
                execution_plan={"historical": True},
                scheduled_for=created + timedelta(seconds=index),
            )
            for index, origin in enumerate(["", "manual", "webhook", "schedule"])
        ]
        old_run.objects.all().update(created_at=created)
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        migrated_run = apps.get_model("runs", "Run")
        task_model = apps.get_model("runs", "Task")

        assert task_model.objects.count() == len(historical)
        for old in historical:
            run = migrated_run.objects.get(pk=old.pk)
            task = run.task
            assert task.job_id == old.job_id
            assert task.origin == old.started_by
            assert task.submitted_by_id is None
            assert task.commit_sha == run.commit_sha == "historical-commit"
            assert task.input_snapshot == run.input_snapshot == snapshot
            assert task.created_at == created
            assert task.scheduled_for == old.scheduled_for
            assert run.execution_plan == {"historical": True}

        # Reapplying the data operation must not duplicate assignments.
        MigrationExecutor(connection).migrate([("runs", "0004_task_assignment")])
        MigrationExecutor(connection).migrate(after)
        assert task_model.objects.count() == len(historical)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
