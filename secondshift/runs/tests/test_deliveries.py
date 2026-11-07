import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.config.constants import RETRY_NOTIFICATION_WORKFLOW
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
from secondshift.runs import deliveries
from secondshift.runs.models import Run, RunDelivery
from secondshift.runs.orchestration import workflows
from secondshift.workspaces.models import Workspace, WorkspaceMembership

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


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Mock:
    client = Mock()
    client.enqueue.side_effect = lambda options, *args: Mock(
        get_workflow_id=lambda: options["workflow_id"]
    )
    monkeypatch.setattr(deliveries, "new_dbos_client", lambda: client)
    return client


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
    assert deliveries.retryable(delivery)


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


@pytest.mark.django_db
def test_a_notification_waiting_for_another_attempt_is_retrying(
    approved_run: tuple[Run, Connection, JobGrant],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, connection, _ = approved_run

    def unavailable(connection: Connection, payload: dict[str, Any]) -> Any:
        return webhooks.WebhookDelivery(False, 503, "Unavailable.", retryable=True)

    monkeypatch.setattr(workflows, "deliver_webhook", unavailable)

    with pytest.raises(workflows.RetryableDeliveryError):
        workflows.deliver_run_notification_step(run.pk, connection.pk)

    delivery = only_delivery(run)
    assert delivery.status == RunDelivery.Status.RETRYING
    assert delivery.attempts == 1


@pytest.mark.django_db
def test_retrying_sends_the_stored_report_again(
    approved_run: tuple[Run, Connection, JobGrant],
    receiver: Receiver,
    queue: Mock,
) -> None:
    run, connection, _ = approved_run
    receiver.codes.append(500)
    workflows.notify(run.pk)
    delivery = only_delivery(run)

    deliveries.retry_delivery(run, delivery.pk)

    delivery.refresh_from_db()
    assert delivery.status == RunDelivery.Status.RETRYING
    options, *arguments = queue.enqueue.call_args.args
    assert options["workflow_name"] == RETRY_NOTIFICATION_WORKFLOW
    assert arguments == [run.pk, connection.pk]

    workflows.deliver_notification(*arguments)

    delivery.refresh_from_db()
    assert delivery.status == RunDelivery.Status.DELIVERED
    assert receiver.sent == [run.pk, run.pk]
    # The agent did not run again; the run's outcome is unchanged.
    run.refresh_from_db()
    assert run.status == Run.Status.SUCCEEDED


@pytest.mark.django_db
def test_a_retry_after_the_grant_is_revoked_is_skipped(
    approved_run: tuple[Run, Connection, JobGrant],
    receiver: Receiver,
    queue: Mock,
) -> None:
    run, connection, grant = approved_run
    receiver.codes.append(500)
    workflows.notify(run.pk)
    deliveries.retry_delivery(run, only_delivery(run).pk)
    grant.status = JobGrant.Status.DENIED
    grant.save()

    workflows.deliver_notification(run.pk, connection.pk)

    delivery = only_delivery(run)
    assert delivery.status == RunDelivery.Status.SKIPPED
    assert receiver.sent == [run.pk]


@pytest.mark.django_db
def test_only_failed_deliveries_with_a_connection_can_be_retried(
    approved_run: tuple[Run, Connection, JobGrant],
    receiver: Receiver,
    queue: Mock,
) -> None:
    run, connection, grant = approved_run
    workflows.notify(run.pk)
    delivery = only_delivery(run)

    with pytest.raises(deliveries.RunRefused, match="Only a failed"):
        deliveries.retry_delivery(run, delivery.pk)

    RunDelivery.objects.filter(pk=delivery.pk).update(status=RunDelivery.Status.FAILED)
    grant.delete()
    connection.delete()
    with pytest.raises(deliveries.RunRefused, match="was removed"):
        deliveries.retry_delivery(run, delivery.pk)
    queue.enqueue.assert_not_called()


@pytest.mark.django_db
def test_a_queue_outage_leaves_the_delivery_failed(
    approved_run: tuple[Run, Connection, JobGrant],
    receiver: Receiver,
    queue: Mock,
) -> None:
    run, _, _ = approved_run
    receiver.codes.append(500)
    workflows.notify(run.pk)
    queue.enqueue.side_effect = ConnectionError("queue down")

    with pytest.raises(deliveries.DeliveryQueueUnavailable):
        deliveries.retry_delivery(run, only_delivery(run).pk)

    assert only_delivery(run).status == RunDelivery.Status.FAILED


@pytest.mark.django_db
def test_retry_endpoint_needs_run_permission(
    client: Client,
    approved_run: tuple[Run, Connection, JobGrant],
    receiver: Receiver,
    queue: Mock,
    setup: Setup,  # noqa: F811
) -> None:
    owner, workspace, _ = setup
    run, _, _ = approved_run
    receiver.codes.append(500)
    workflows.notify(run.pk)
    delivery = only_delivery(run)
    url = f"/api/runs/{run.pk}/deliveries/{delivery.pk}/retry"
    member = get_user_model().objects.create_user(username="member")
    WorkspaceMembership.objects.create(
        workspace=workspace, user=member, role=WorkspaceMembership.Role.MEMBER
    )

    client.force_login(member)
    listed = client.get(f"/api/runs/{run.pk}").json()["deliveries"]
    assert [(item["status"], item["retryable"]) for item in listed] == [
        ("failed", True)
    ]
    assert client.post(url).status_code == 403

    client.force_login(owner)
    response = client.post(url)
    assert response.status_code == 200
    assert response.json()["status"] == "retrying"
    assert client.post(url).status_code == 409
    assert client.post(f"/api/runs/{run.pk}/deliveries/999/retry").status_code == 404
