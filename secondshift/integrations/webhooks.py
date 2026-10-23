import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import (
    OutboundUrlError,
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
        response = requests.post(
            url,
            json=payload,
            headers={"User-Agent": USER_AGENT},
            timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.Timeout:
        return WebhookDelivery(False, None, "The webhook timed out.", retryable=True)
    except requests.RequestException as error:
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
