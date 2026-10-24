import logging
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings

from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import (
    OutboundUrlError,
    PinnedSyncTransport,
    host_label,
    validate_outbound_url,
)

logger = logging.getLogger(__name__)

USER_AGENT = "SecondShift-Webhook/1"


WebhookError = OutboundUrlError


@dataclass(frozen=True)
class WebhookDelivery:
    ok: bool
    status_code: int | None
    message: str
    retryable: bool = False


def validate_webhook_url(url: str) -> str:
    return validate_outbound_url(url, "webhook URL")


def webhook_target_label(url: str) -> str:
    return host_label(url)


def webhook_transport() -> httpx.BaseTransport:
    # Connects only to the addresses it checked, so DNS cannot rebind
    # between the check and the request.
    return PinnedSyncTransport()


def deliver_webhook(connection: Connection, payload: dict[str, Any]) -> WebhookDelivery:
    if (
        connection.provider != Connection.Provider.WEBHOOK
        or not connection.delivery_url
    ):
        return WebhookDelivery(False, None, "This connection has no webhook URL.")
    try:
        # Validated again at send time: DNS answers can change after saving.
        url = validate_webhook_url(connection.delivery_url)
    except WebhookError as error:
        return WebhookDelivery(False, None, str(error))
    try:
        with httpx.Client(
            transport=webhook_transport(),
            timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = client.post(
                url, json=payload, headers={"User-Agent": USER_AGENT}
            )
    except httpx.TimeoutException:
        return WebhookDelivery(False, None, "The webhook timed out.", retryable=True)
    except httpx.HTTPError as error:
        # Exception text includes the URL, which may embed a token.
        logger.warning(
            "webhook delivery to connection %s failed: %s",
            connection.pk,
            type(error).__name__,
        )
        return WebhookDelivery(
            False, None, "The webhook could not be reached.", retryable=True
        )
    status = response.status_code
    if 200 <= status < 300:
        return WebhookDelivery(True, status, f"Delivered (HTTP {status}).")
    if 300 <= status < 400:
        return WebhookDelivery(
            False,
            status,
            f"The webhook redirected (HTTP {status}); redirects are not followed.",
        )
    return WebhookDelivery(
        False,
        status,
        f"The webhook answered HTTP {status}.",
        retryable=status == 429 or status >= 500,
    )


def connection_test_payload(connection: Connection) -> dict[str, Any]:
    return {
        "event": "connection.test",
        "text": (
            f"SecondShift test message for {connection.binding_key}. "
            "Run reports for jobs using this binding will arrive here."
        ),
        "binding": connection.binding_key,
    }
