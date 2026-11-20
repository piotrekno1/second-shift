"""Delivering a finished run's results again, without running the agent again."""

import logging
import uuid

from dbos import EnqueueOptions
from django.conf import settings
from django.db import transaction

from secondshift.runs.constants import (
    RETRY_HAND_BACK_WORKFLOW,
    RETRY_ISSUES_WORKFLOW,
    RETRY_NOTIFICATION_WORKFLOW,
    RETRY_PULL_REQUEST_WORKFLOW,
)
from secondshift.runs.dao import add_event, lock_delivery
from secondshift.runs.models import Run, RunDelivery
from secondshift.runs.orchestration.dbos_runtime import new_dbos_client
from secondshift.runs.services import RunRefused

logger = logging.getLogger(__name__)


class DeliveryQueueUnavailable(Exception):
    pass


GITHUB_DELIVERIES = frozenset(
    {
        RunDelivery.Kind.PULL_REQUEST,
        RunDelivery.Kind.ISSUES,
        RunDelivery.Kind.HAND_BACK,
    }
)


def retryable(delivery: RunDelivery) -> bool:
    # Pull requests, issues and hand-backs need no connection. The
    # repository's own GitHub access sends them.
    return delivery.status == RunDelivery.Status.FAILED and (
        delivery.kind in GITHUB_DELIVERIES or delivery.connection_id is not None
    )


@transaction.atomic
def retry_delivery(run: Run, delivery_id: int) -> RunDelivery:
    """Queue a failed delivery again. Authorization is checked when it is sent."""
    delivery = lock_delivery(run, delivery_id)
    if delivery.status != RunDelivery.Status.FAILED:
        raise RunRefused("Only a failed delivery can be retried.")
    if not retryable(delivery):
        raise RunRefused("The connection for this delivery was removed.")
    delivery.status = RunDelivery.Status.RETRYING
    delivery.message = "Waiting to retry."
    delivery.save(update_fields=["status", "message", "updated_at"])
    # Enqueued inside the transaction, so a queue outage leaves it failed.
    if delivery.kind == RunDelivery.Kind.PULL_REQUEST:
        add_event(run.pk, "Retrying the pull request.")
        enqueue_retry(RETRY_PULL_REQUEST_WORKFLOW, f"pull-request-{run.pk}", run.pk)
    elif delivery.kind == RunDelivery.Kind.ISSUES:
        add_event(run.pk, "Retrying the issues.")
        enqueue_retry(RETRY_ISSUES_WORKFLOW, f"issues-{run.pk}", run.pk)
    elif delivery.kind == RunDelivery.Kind.HAND_BACK:
        add_event(run.pk, "Retrying the hand-back.")
        enqueue_retry(RETRY_HAND_BACK_WORKFLOW, f"hand-back-{run.pk}", run.pk)
    else:
        add_event(run.pk, f"Retrying the notification to {delivery.target}.")
        enqueue_retry(
            RETRY_NOTIFICATION_WORKFLOW,
            f"{run.pk}-{delivery.connection_id}",
            run.pk,
            delivery.connection_id,
        )
    return delivery


def enqueue_retry(workflow: str, name: str, *args: int) -> str:
    suffix = uuid.uuid4().hex[:12]
    options: EnqueueOptions = {
        "workflow_name": workflow,
        "queue_name": settings.DBOS_QUEUE_NAME,
        # Each retry is its own workflow. A reused ID would return the old result.
        "workflow_id": f"secondshift-delivery-{name}-{suffix}",
        "application_name": settings.DBOS_APPLICATION_NAME,
    }
    try:
        client = new_dbos_client()
    except Exception as error:
        raise DeliveryQueueUnavailable from error
    try:
        return client.enqueue(options, *args).get_workflow_id()
    except Exception as error:
        raise DeliveryQueueUnavailable from error
    finally:
        # DBOS may have accepted the retry already. Raising here would roll
        # the delivery back to failed while the retry runs, and a second
        # retry would send it twice.
        try:
            client.destroy()
        except Exception:
            logger.warning("Could not close the queue client for %s", name)
