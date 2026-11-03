import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from django.contrib.auth.models import AbstractUser

from secondshift.connections import webhooks
from secondshift.connections.models import Connection
from secondshift.jobs.models import JobGrant
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.tests.test_needs import (
    alerts_webhook,
    make_job,
    notify_request,
    setup,  # noqa: F401 (fixture)
)
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run, RunDelivery
from secondshift.runs.orchestration import workflows
from secondshift.workspaces.models import Workspace

Setup = tuple[AbstractUser, Workspace, Repository]


@pytest.fixture
def approved_run(settings: Any, setup: Setup) -> tuple[Run, Connection, JobGrant]:  # noqa: F811
    settings.OUTBOUND_ALLOW_LOCAL = True
    user, workspace, repository = setup
    job = make_job(repository, "health")
    connection = alerts_webhook(workspace)
    grant = JobGrant.objects.create(
        capability_request=notify_request(job),
        connection=connection,
        status=JobGrant.Status.APPROVED,
        reviewed_by=user,
    )
    run = Run.objects.create(
        job=job,
        status=Run.Status.SUCCEEDED,
        commit_sha="abc123",
        scheduled_for=datetime(2026, 10, 1, tzinfo=UTC),
        summary="All good.",
        execution_plan=build_execution_plan(job),
    )
    return run, connection, grant


@dataclass
class Receiver:
    """Answers each webhook with the next status code, then 200."""

    codes: list[int] = field(default_factory=list)
    sent: list[int] = field(default_factory=list)

    def receive(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content)["run"]["id"])
        return httpx.Response(self.codes.pop(0) if self.codes else 200)


@pytest.fixture
def receiver(monkeypatch: pytest.MonkeyPatch) -> Receiver:
    target = Receiver()
    monkeypatch.setattr(
        webhooks, "webhook_transport", lambda: httpx.MockTransport(target.receive)
    )
    return target


def only_delivery(run: Run) -> RunDelivery:
    return RunDelivery.objects.get(run=run)


@pytest.mark.django_db
def test_a_sent_notification_is_recorded_and_never_sent_twice(
    approved_run: tuple[Run, Connection, JobGrant], receiver: Receiver
) -> None:
    run, _, _ = approved_run

    workflows.notify(run.pk)
    workflows.notify(run.pk)

    delivery = only_delivery(run)
    assert delivery.status == RunDelivery.Status.DELIVERED
    assert delivery.target == "engineering-alerts"
    assert delivery.attempts == 1
    assert receiver.sent == [run.pk]


@pytest.mark.django_db
def test_a_rejected_notification_is_failed_and_retryable(
    approved_run: tuple[Run, Connection, JobGrant], receiver: Receiver
) -> None:
    run, _, _ = approved_run
    receiver.codes.append(404)

    workflows.notify(run.pk)

    delivery = only_delivery(run)
    assert delivery.status == RunDelivery.Status.FAILED
    assert delivery.status_code == 404


@pytest.mark.django_db
def test_a_notification_that_keeps_erroring_ends_failed(
    approved_run: tuple[Run, Connection, JobGrant],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = approved_run

    def unavailable(connection: Connection, payload: dict[str, Any]) -> Any:
        return webhooks.WebhookDelivery(False, 503, "Unavailable.", retryable=True)

    monkeypatch.setattr(workflows, "deliver_webhook", unavailable)

    workflows.notify(run.pk)

    delivery = only_delivery(run)
    assert delivery.status == RunDelivery.Status.FAILED
    assert delivery.message == "Unavailable."
