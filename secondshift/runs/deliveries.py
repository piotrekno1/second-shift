"""Sending a finished run's report again, without running the agent again."""

import uuid

from dbos import EnqueueOptions
from django.conf import settings
from django.db import transaction

from secondshift.config.constants import RETRY_NOTIFICATION_WORKFLOW
from secondshift.runs.models import Run, RunDelivery, RunEvent
from secondshift.runs.orchestration.dbos_runtime import new_dbos_client
from secondshift.runs.services import RunRefused


class DeliveryQueueUnavailable(Exception):
    pass


def retryable(delivery: RunDelivery) -> bool:
    return (
        delivery.status == RunDelivery.Status.FAILED
        and delivery.connection_id is not None
    )


@transaction.atomic
def retry_delivery(run: Run, delivery_id: int) -> RunDelivery:
    """Queue a failed delivery again. Authorization is checked when it is sent."""
    delivery = (
        RunDelivery.objects.select_for_update()
        .select_related("connection")
        .get(pk=delivery_id, run=run)
    )
    if delivery.status != RunDelivery.Status.FAILED:
        raise RunRefused("Only a failed delivery can be retried.")
    if delivery.connection_id is None:
        raise RunRefused("The connection for this delivery was removed.")
    delivery.status = RunDelivery.Status.PENDING
    delivery.message = "Waiting to retry."
    delivery.save(update_fields=["status", "message", "updated_at"])
    RunEvent.objects.create(
        run=run, message=f"Retrying the notification to {delivery.target}."
    )
    # Enqueued inside the transaction, so a queue outage leaves it failed.
    enqueue_notification_retry(run.pk, delivery.connection_id)
    return delivery


def enqueue_notification_retry(run_id: int, connection_id: int) -> str:
    suffix = uuid.uuid4().hex[:12]
    options: EnqueueOptions = {
        "workflow_name": RETRY_NOTIFICATION_WORKFLOW,
        "queue_name": settings.DBOS_QUEUE_NAME,
        # Each retry is its own workflow. A reused ID would return the old result.
        "workflow_id": f"secondshift-delivery-{run_id}-{connection_id}-{suffix}",
        "application_name": settings.DBOS_APPLICATION_NAME,
    }
    try:
        client = new_dbos_client()
    except Exception as error:
        raise DeliveryQueueUnavailable from error
    try:
        return client.enqueue(options, run_id, connection_id).get_workflow_id()
    except Exception as error:
        raise DeliveryQueueUnavailable from error
    finally:
        client.destroy()
