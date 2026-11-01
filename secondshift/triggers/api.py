import logging

from django.conf import settings
from django.http import HttpRequest
from ninja import Router, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.config.constants import TriggerKind
from secondshift.jobs.access import job_for_request
from secondshift.triggers.models import JobTrigger
from secondshift.triggers.schemas import (
    TriggerChangeSchema,
    TriggerEnabledSchema,
    TriggerReceivedSchema,
    TriggerSchema,
)
from secondshift.triggers.serialization import (
    serialize_change,
    serialize_job_triggers,
)
from secondshift.triggers.services import (
    TriggerError,
    receive,
    rotate_secret,
    set_trigger_enabled,
)
from secondshift.workspaces.access import Action, require

logger = logging.getLogger(__name__)

router = Router(auth=django_auth, tags=["triggers"])


@router.get("/jobs/{job_id}/triggers", response=list[TriggerSchema])
def job_triggers(request: HttpRequest, job_id: int) -> list[TriggerSchema]:
    return serialize_job_triggers(job_for_request(request, job_id))


@router.post("/jobs/{job_id}/triggers/{kind}/enabled", response=TriggerChangeSchema)
def set_enabled(
    request: HttpRequest, job_id: int, kind: TriggerKind, payload: TriggerEnabledSchema
) -> TriggerChangeSchema:
    job = job_for_request(request, job_id)
    require(request, Action.MANAGE_TRIGGERS)
    try:
        change = set_trigger_enabled(job, kind, payload.enabled)
    except TriggerError as error:
        raise HttpError(409, str(error)) from error
    return serialize_change(job, change)


@router.post("/jobs/{job_id}/triggers/{kind}/rotate", response=TriggerChangeSchema)
def rotate(request: HttpRequest, job_id: int, kind: TriggerKind) -> TriggerChangeSchema:
    job = job_for_request(request, job_id)
    require(request, Action.MANAGE_TRIGGERS)
    try:
        change = rotate_secret(job, kind)
    except TriggerError as error:
        raise HttpError(409, str(error)) from error
    return serialize_change(job, change)


@router.post(
    "/triggers/{public_id}",
    auth=None,
    response={
        frozenset({200, 202, 401, 409, 413, 429}): TriggerReceivedSchema,
    },
    include_in_schema=False,
)
def receive_trigger(
    request: HttpRequest, public_id: str
) -> Status[TriggerReceivedSchema]:
    """The public URL a webhook calls. Its secret is the only sign-in."""
    trigger = (
        JobTrigger.objects.select_related("job__repository")
        .filter(public_id=public_id)
        .first()
    )
    if trigger is None:
        raise HttpError(404, "Not found.")
    length = int(request.headers.get("Content-Length") or 0)
    if length > settings.TRIGGER_MAX_BODY_BYTES:
        return Status(
            413, TriggerReceivedSchema(detail="The request body is too large.")
        )
    body = request.body
    if len(body) > settings.TRIGGER_MAX_BODY_BYTES:
        return Status(
            413, TriggerReceivedSchema(detail="The request body is too large.")
        )
    received = receive(trigger, request.headers, body)
    if received.run is None:
        logger.info("trigger %s did not start a run: %s", trigger.pk, received.detail)
    return Status(
        received.status,
        TriggerReceivedSchema(
            detail=received.detail,
            run_id=received.run.pk if received.run else None,
        ),
    )
