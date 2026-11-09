"""Queue acceptance, recovery and execution boundaries without an agent call."""

import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from dbos import DBOS, EnqueueOptions
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone
from pydantic import ValidationError

from secondshift.config.constants import RunStart
from secondshift.jobs.models import Job
from secondshift.jobs.tests.plans import stored_plan
from secondshift.mcp_server.tests.test_job_tools import call, token
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run, RunEvent, Task
from secondshift.runs.orchestration import dbos_runtime, dispatch, workflows
from secondshift.runs.queue import cancel_queued_task
from secondshift.runs.services import RunRefused
from secondshift.runs.tests.test_submissions import (
    owner as owner_fixture,
)
from secondshift.runs.tests.test_submissions import (
    repository as repository_fixture,
)
from secondshift.runs.tests.test_submissions import (
    start,
    submission,
)
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import WorkspaceMembership

owner = owner_fixture
repository = repository_fixture

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client_queue(monkeypatch: pytest.MonkeyPatch) -> Mock:
    client = Mock()
    client.enqueue.side_effect = lambda options, run_id: Mock(
        get_workflow_id=lambda: options["workflow_id"]
    )
    monkeypatch.setattr(dispatch, "new_dbos_client", lambda: client)
    return client


def test_backlog_advances_only_after_predecessor_finishes(
    repository: Repository, owner: User, client_queue: Mock
) -> None:
    runs = [start(repository, owner, idempotency_key=f"task-{n}") for n in range(3)]
    assert client_queue.enqueue.call_count == 1
    assert dispatch.dispatch_waiting_tasks() == 0
    for index, run in enumerate(runs):
        run.refresh_from_db()
        assert run.dbos_workflow_id == f"secondshift-run-{run.pk}"
        Run.objects.filter(pk=run.pk).update(status=Run.Status.SUCCEEDED)
        assert dispatch.dispatch_waiting_tasks() == (1 if index < 2 else 0)
    assert client_queue.enqueue.call_count == 3


def test_future_task_does_not_block_ready_work(
    repository: Repository, owner: User, client_queue: Mock
) -> None:
    future = timezone.now() + timedelta(hours=8)
    delayed = start(repository, owner, not_before=future)
    ready = start(repository, owner, idempotency_key="ready")
    assert delayed.scheduled_for == delayed.task.scheduled_for == future
    assert client_queue.enqueue.call_args.args[1] == ready.pk
    Run.objects.filter(pk=ready.pk).update(status=Run.Status.SUCCEEDED)
    assert dispatch.dispatch_waiting_tasks() == 0
    Run.objects.filter(pk=delayed.pk).update(scheduled_for=timezone.now())
    assert dispatch.dispatch_waiting_tasks() == 1


def test_ready_order_uses_requested_start_then_acceptance(
    repository: Repository,
    owner: User,
    client_queue: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("secondshift.runs.submissions.enqueue_run", lambda run: None)
    late = start(repository, owner)
    earlier = start(
        repository,
        owner,
        idempotency_key="earlier",
        not_before=timezone.now() - timedelta(hours=1),
    )
    assert dispatch.dispatch_waiting_tasks() == 1
    assert client_queue.enqueue.call_args.args[1] == earlier.pk
    late.refresh_from_db()
    assert not late.dbos_workflow_id


@pytest.mark.parametrize("where", ["connect", "enqueue"])
def test_queue_unavailable_remains_accepted_then_recovers(
    repository: Repository,
    owner: User,
    client_queue: Mock,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    if where == "connect":
        monkeypatch.setattr(
            dispatch,
            "new_dbos_client",
            Mock(side_effect=RuntimeError("private connection detail")),
        )
    else:
        client_queue.enqueue.side_effect = RuntimeError("private connection detail")
    run = start(repository, owner)
    run.refresh_from_db()
    assert run.status == Run.Status.QUEUED and not run.dbos_workflow_id
    assert dispatch.dispatch_waiting_tasks() == 0
    assert RunEvent.objects.filter(run=run).count() == 1
    assert "private" not in RunEvent.objects.get(run=run).message
    monkeypatch.setattr(dispatch, "new_dbos_client", lambda: client_queue)
    client_queue.enqueue.side_effect = lambda options, run_id: Mock(
        get_workflow_id=lambda: options["workflow_id"]
    )
    assert dispatch.dispatch_waiting_tasks() == 1
    run.refresh_from_db()
    assert run.dbos_workflow_id == f"secondshift-run-{run.pk}"


def test_lost_handoff_ack_retries_same_workflow_identity(
    repository: Repository, owner: User, client_queue: Mock
) -> None:
    client_queue.enqueue.side_effect = RuntimeError(
        "lost acknowledgement after acceptance"
    )
    run = start(repository, owner)
    first_options = client_queue.enqueue.call_args.args[0].copy()
    client_queue.enqueue.side_effect = lambda options, run_id: Mock(
        get_workflow_id=lambda: options["workflow_id"]
    )
    assert dispatch.dispatch_waiting_tasks() == 1
    assert client_queue.enqueue.call_args.args == (first_options, run.pk)
    assert dispatch.dispatch_waiting_tasks() == 0


def test_real_dbos_recovers_lost_ack_without_duplicate_workflows(
    repository: Repository,
    owner: User,
    settings: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.DBOS_SYSTEM_DATABASE_URL = f"sqlite:///{tmp_path / 'workflow.sqlite'}"
    DBOS.migrate(settings.DBOS_SYSTEM_DATABASE_URL)
    lose_ack = True

    def client_with_lost_ack() -> Any:
        client = dbos_runtime.new_dbos_client()
        enqueue = client.enqueue

        def accept(options: EnqueueOptions, run_id: int) -> Any:
            nonlocal lose_ack
            handle = enqueue(options, run_id)
            if lose_ack:
                lose_ack = False
                raise RuntimeError("Lost acknowledgement")
            return handle

        client.enqueue = accept
        return client

    monkeypatch.setattr(dispatch, "new_dbos_client", client_with_lost_ack)
    run = start(repository, owner)
    run.refresh_from_db()
    assert run.dbos_workflow_id == ""
    assert dispatch.dispatch_waiting_tasks() == 1
    run.refresh_from_db()
    client = dbos_runtime.new_dbos_client()
    try:
        workflows = client.list_workflows(workflow_ids=[run.dbos_workflow_id])
        assert len(workflows) == 1
        assert workflows[0].status == "ENQUEUED"
    finally:
        client.destroy()


def test_client_cleanup_failure_does_not_lose_accepted_workflow(
    repository: Repository,
    owner: User,
    client_queue: Mock,
) -> None:
    client_queue.destroy.side_effect = RuntimeError("cleanup failed")
    run = start(repository, owner)
    run.refresh_from_db()
    assert run.status == Run.Status.QUEUED
    assert run.dbos_workflow_id == f"secondshift-run-{run.pk}"
    assert dispatch.dispatch_waiting_tasks() == 0


def test_committed_run_recovers_without_submission_callback(
    repository: Repository,
    owner: User,
    client_queue: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("secondshift.runs.submissions.enqueue_run", lambda run: None)
    run = start(repository, owner)
    assert client_queue.enqueue.call_count == 0
    assert dispatch.dispatch_waiting_tasks() == 1
    assert client_queue.enqueue.call_args.args[1] == run.pk


def test_capacity_limit_preserves_idempotent_retry(
    repository: Repository, owner: User, client_queue: Mock, settings: Any
) -> None:
    settings.TASK_QUEUE_LIMIT = 2
    first = start(repository, owner)
    second = start(repository, owner, idempotency_key="second")
    assert start(repository, owner).pk == first.pk
    with pytest.raises(RunRefused, match="queue is full"):
        start(repository, owner, idempotency_key="third")
    cancel_queued_task(second)
    assert start(repository, owner, idempotency_key="third").pk != second.pk
    assert Task.objects.count() == 3


def test_repository_slots_are_independent(
    repository: Repository,
    owner: User,
    client_queue: Mock,
) -> None:
    first = start(repository, owner)
    other_path = Path(repository.local_path).with_name("other-project")
    shutil.copytree(repository.local_path, other_path)
    other = Repository.objects.create(
        workspace=repository.workspace,
        source_type=repository.source_type,
        local_path=str(other_path),
        full_name="other/project",
        default_branch="main",
        enabled=True,
    )
    following = start(other, owner)
    assert client_queue.enqueue.call_count == 2
    assert following.pk != first.pk


def test_cancel_is_workspace_scoped_and_rejects_saved_job_runs(
    repository: Repository,
    owner: User,
    client_queue: Mock,
) -> None:
    run = start(repository, owner)
    stranger = User.objects.create_user(username="stranger")
    create_workspace("Other", "other", stranger)
    client = Client()
    client.force_login(stranger)
    assert client.post(f"/api/runs/{run.pk}/cancel").status_code == 404
    assert (
        call(token(stranger, "run"), "cancel_task", run_id=run.pk).get("isError")
        is True
    )
    job = Job.objects.create(
        workspace=repository.workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        config_hash="hash",
    )
    legacy = Run.objects.create(
        started_by=RunStart.SCHEDULE,
        execution_plan=stored_plan(),
        job=job,
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
    )
    client.force_login(owner)
    assert client.post(f"/api/runs/{legacy.pk}/cancel").status_code == 409
    legacy.refresh_from_db()
    assert legacy.status == Run.Status.QUEUED


def test_not_before_needs_timezone_and_is_part_of_retry_identity(
    repository: Repository, owner: User, client_queue: Mock
) -> None:
    with pytest.raises(ValidationError):
        submission(repository, not_before="2026-11-04T08:00:00")
    when = timezone.now() + timedelta(days=1)
    start(repository, owner, not_before=when)
    with pytest.raises(RunRefused, match="different submission"):
        start(repository, owner, not_before=when + timedelta(hours=1))


@pytest.mark.parametrize("change", ["disabled", "removed", "membership", "inactive"])
def test_delayed_dispatch_revalidates_access(
    repository: Repository,
    owner: User,
    client_queue: Mock,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    monkeypatch.setattr("secondshift.runs.submissions.enqueue_run", lambda run: None)
    run = start(repository, owner)
    if change == "disabled":
        Repository.objects.filter(pk=repository.pk).update(enabled=False)
    elif change == "removed":
        Repository.objects.filter(pk=repository.pk).update(removed_at=timezone.now())
    elif change == "membership":
        WorkspaceMembership.objects.filter(
            workspace=repository.workspace, user=owner
        ).update(role="member")
    else:
        owner.is_active = False
        owner.save()
    assert dispatch.dispatch_waiting_tasks() == 0
    run.refresh_from_db()
    assert run.status == Run.Status.FAILED and run.finished_at is not None
    client_queue.enqueue.assert_not_called()


def test_access_change_after_handoff_stops_before_checkout(
    repository: Repository,
    owner: User,
    client_queue: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = start(repository, owner)
    Repository.objects.filter(pk=repository.pk).update(enabled=False)
    prepare = Mock()
    monkeypatch.setattr(workflows, "prepare_source_step", prepare)
    workflows.execute_run(run.pk)
    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    prepare.assert_not_called()


def test_cancel_handed_off_task_prevents_execution_and_releases_slot(
    repository: Repository,
    owner: User,
    client_queue: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = start(repository, owner)
    second = start(repository, owner, idempotency_key="second")
    cancel_queued_task(first)
    prepare = Mock()
    monkeypatch.setattr(workflows, "prepare_source_step", prepare)
    workflows.execute_run(first.pk)
    prepare.assert_not_called()
    assert dispatch.dispatch_waiting_tasks() == 1
    assert client_queue.enqueue.call_args.args[1] == second.pk
    assert cancel_queued_task(first).status == Run.Status.CANCELED


def test_cannot_cancel_running_task(
    repository: Repository, owner: User, client_queue: Mock
) -> None:
    run = start(repository, owner)
    Run.objects.filter(pk=run.pk).update(status=Run.Status.RUNNING)
    with pytest.raises(RunRefused, match="already started"):
        cancel_queued_task(run)


def test_cancel_http_and_mcp_enforce_scope(
    repository: Repository, owner: User, client_queue: Mock
) -> None:
    run = start(repository, owner)
    denied = call(token(owner, "read"), "cancel_task", run_id=run.pk)
    assert denied.get("isError") is True
    accepted = call(token(owner, "run"), "cancel_task", run_id=run.pk)
    assert accepted["structuredContent"]["run"]["status"] == "canceled"
    client = Client()
    client.force_login(owner)
    response = client.post(f"/api/runs/{run.pk}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


def test_queued_start_step_replay_keeps_original_start(
    repository: Repository,
    owner: User,
    client_queue: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = start(repository, owner)
    monkeypatch.setattr(
        workflows,
        "DBOS",
        Mock(workflow_id=run.dbos_workflow_id or f"secondshift-run-{run.pk}"),
    )
    assert workflows.mark_run_started_step(run.pk) is True
    run.refresh_from_db()
    started_at = run.started_at
    assert workflows.mark_run_started_step(run.pk) is True
    run.refresh_from_db()
    assert run.started_at == started_at
    assert RunEvent.objects.filter(run=run, message="Run started.").count() == 1


@pytest.mark.parametrize("limit,worker", [(0, 4), (2, 2), (8, 4)])
def test_global_limit_is_registered_and_worker_limit_fits(
    settings: Any, monkeypatch: pytest.MonkeyPatch, limit: int, worker: int
) -> None:
    settings.DBOS_WORKER_CONCURRENCY = 4
    settings.DBOS_GLOBAL_CONCURRENCY = limit
    dbos = Mock()
    monkeypatch.setattr(dbos_runtime, "DBOS", dbos)
    dbos_runtime.launch_dbos_worker()
    dbos.register_queue.assert_called_once_with(
        settings.DBOS_QUEUE_NAME,
        worker_concurrency=worker,
        global_concurrency=limit or None,
    )
