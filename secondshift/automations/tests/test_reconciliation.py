from types import SimpleNamespace
from typing import Any, cast

import pytest
from dbos import DBOSClient
from django.contrib.auth import get_user_model
from django.utils import timezone

from secondshift.automations.models import Job, Run
from secondshift.automations.reconciliation import reconcile_terminal_workflows
from secondshift.core.services import create_workspace
from secondshift.repositories.models import Repository


class FakeDBOSClient:
    def __init__(self, statuses: list[SimpleNamespace]) -> None:
        self.statuses = statuses

    def list_workflows(self, **kwargs: Any) -> list[SimpleNamespace]:
        workflow_ids = set(kwargs["workflow_ids"])
        return [
            status for status in self.statuses if status.workflow_id in workflow_ids
        ]


def create_run(*, status: str, workflow_id: str) -> Run:
    user = get_user_model().objects.create_user(username=f"owner-{workflow_id}")
    workspace = create_workspace(
        name=f"Workspace {workflow_id}",
        slug=f"workspace-{workflow_id}",
        owner=user,
    )
    repository = Repository.objects.create(
        workspace=workspace,
        full_name=f"local/{workflow_id}",
        enabled=True,
        config_sha="abc123",
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        config_hash="config-hash",
        enabled=True,
    )
    return Run.objects.create(
        job=job,
        status=status,
        commit_sha="abc123",
        scheduled_for=timezone.now(),
        dbos_workflow_id=workflow_id,
    )


@pytest.mark.django_db
def test_reconcile_marks_error_workflow_as_failed_once() -> None:
    run = create_run(status=Run.Status.RUNNING, workflow_id="run-error")
    fake = FakeDBOSClient([SimpleNamespace(workflow_id="run-error", status="ERROR")])

    first = reconcile_terminal_workflows(cast(DBOSClient, fake))
    second = reconcile_terminal_workflows(cast(DBOSClient, fake))

    run.refresh_from_db()
    assert first == 1
    assert second == 0
    assert run.status == Run.Status.FAILED
    assert run.finished_at is not None
    assert run.summary == "The durable workflow failed before the run completed."
    assert run.events.get().metadata == {"workflow_status": "ERROR"}


@pytest.mark.django_db
def test_reconcile_preserves_canceled_workflow_status() -> None:
    run = create_run(status=Run.Status.QUEUED, workflow_id="run-canceled")
    fake = FakeDBOSClient(
        [SimpleNamespace(workflow_id="run-canceled", status="CANCELLED")]
    )

    reconciled = reconcile_terminal_workflows(cast(DBOSClient, fake))

    run.refresh_from_db()
    assert reconciled == 1
    assert run.status == Run.Status.CANCELED
