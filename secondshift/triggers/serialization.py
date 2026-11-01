from django.conf import settings

from secondshift.config.constants import TriggerKind
from secondshift.jobs.models import Job
from secondshift.triggers.models import JobTrigger
from secondshift.triggers.schemas import (
    TriggerChangeSchema,
    TriggerEventSchema,
    TriggerSchema,
)
from secondshift.triggers.services import TriggerChange

# How many received requests the job page shows for each trigger.
EVENTS_SHOWN = 20


def trigger_url(trigger: JobTrigger) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/api/triggers/{trigger.public_id}"


def serialize_trigger(
    job: Job, kind: TriggerKind, trigger: JobTrigger | None
) -> TriggerSchema:
    events = trigger.events.all()[:EVENTS_SHOWN] if trigger else []
    return TriggerSchema(
        kind=kind,
        declared=kind in job.triggers,
        set_up=trigger is not None,
        enabled=bool(trigger and trigger.enabled),
        url=trigger_url(trigger) if trigger else None,
        secret_rotated_at=trigger.secret_rotated_at if trigger else None,
        events=[
            TriggerEventSchema(
                id=event.pk,
                outcome=event.outcome,
                reason=event.reason,
                run_id=event.run_id,
                received_at=event.received_at,
            )
            for event in events
        ],
    )


def serialize_job_triggers(job: Job) -> list[TriggerSchema]:
    """Every trigger the job names, and any set up earlier that it no longer names."""
    existing = {TriggerKind(item.kind): item for item in job.job_triggers.all()}
    kinds = [TriggerKind(kind) for kind in job.triggers] + [
        kind for kind in existing if kind not in job.triggers
    ]
    return [serialize_trigger(job, kind, existing.get(kind)) for kind in kinds]


def serialize_change(job: Job, change: TriggerChange) -> TriggerChangeSchema:
    trigger = change.trigger
    return TriggerChangeSchema(
        trigger=serialize_trigger(job, TriggerKind(trigger.kind), trigger),
        secret=change.secret,
    )
