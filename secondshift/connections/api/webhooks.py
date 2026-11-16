"""Webhook connections that receive run reports."""

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.api.shared import apply_approval, owned_connection
from secondshift.connections.schemas import (
    ConnectionSchema,
    WebhookConnectionCreateSchema,
    WebhookConnectionUpdateSchema,
    WebhookSampleSchema,
)
from secondshift.connections.services import (
    ConnectionConflict,
    create_webhook_connection,
    update_webhook_connection,
)
from secondshift.connections.webhooks import WebhookError
from secondshift.runs.execution.result import SUMMARY_MAX_CHARS
from secondshift.runs.reporting.notifications import sample_run_report_payload
from secondshift.workspaces.access import Action, require

router = Router(auth=django_auth, tags=["connections"])


@router.post("/connections/webhook", response=ConnectionSchema, auth=django_auth)
def create_webhook(
    request: HttpRequest, payload: WebhookConnectionCreateSchema
) -> ConnectionSchema:
    workspace = require(request, Action.ADD_CONNECTIONS)
    try:
        connection = create_webhook_connection(
            workspace, payload.binding_key, payload.display_name, payload.url
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except WebhookError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return apply_approval(connection, payload.require_approval)


@router.post(
    "/connections/{connection_id}/webhook", response=ConnectionSchema, auth=django_auth
)
def update_webhook(
    request: HttpRequest, connection_id: int, payload: WebhookConnectionUpdateSchema
) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, Action.EDIT_CONNECTIONS)
    try:
        connection = update_webhook_connection(
            connection, payload.display_name, payload.url
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except WebhookError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return apply_approval(connection, payload.require_approval)


@router.get(
    "/connections/webhook/sample", response=WebhookSampleSchema, auth=django_auth
)
def webhook_sample(request: HttpRequest) -> WebhookSampleSchema:
    """What a webhook connection will receive after each run."""
    return WebhookSampleSchema(
        payload=sample_run_report_payload(), summary_max_chars=SUMMARY_MAX_CHARS
    )
