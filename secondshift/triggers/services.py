"""Turn job triggers on and off, and start runs from the requests they receive.

The repository names the trigger. Turning it on creates its URL and secret.
"""

import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from secondshift.config.constants import RunStart, TriggerKind
from secondshift.jobs.models import Job
from secondshift.runs.models import Run
from secondshift.runs.services import RunInProgress, RunNotReady, RunRefused, start_run
from secondshift.triggers.models import JobTrigger, TriggerEvent
from secondshift.triggers.verification import verify

# Secret scanners can match trigger secrets by this prefix.
SECRET_PREFIX = "sst_"

RUN_STARTS = {TriggerKind.WEBHOOK: RunStart.WEBHOOK}


class TriggerError(Exception):
    pass


@dataclass(frozen=True)
class TriggerChange:
    trigger: JobTrigger
    # Only when a secret was just created or rotated. It is shown once.
    secret: str | None = None


@dataclass(frozen=True)
class Received:
    """What a trigger request led to. status is the HTTP status to answer."""

    status: int
    detail: str
    run: Run | None = None


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def declared(job: Job, kind: TriggerKind) -> bool:
    return kind in job.triggers


def set_trigger_enabled(job: Job, kind: TriggerKind, enabled: bool) -> TriggerChange:
    """Turn a trigger on or off. The first time on creates its URL and secret."""
    trigger = JobTrigger.objects.filter(job=job, kind=kind).first()
    if enabled and not declared(job, kind):
        raise TriggerError(
            f"The job's configuration does not name a {kind} trigger. "
            f"Add triggers: [{kind}] to the job in .secondshift.yaml."
        )
    if trigger is None:
        if not enabled:
            raise TriggerError("This trigger is not set up.")
        secret = new_secret()
        trigger = JobTrigger.objects.create(
            job=job,
            kind=kind,
            secret=secret,
            enabled=True,
            secret_rotated_at=timezone.now(),
        )
        return TriggerChange(trigger, secret)
    trigger.enabled = enabled
    trigger.save(update_fields=["enabled", "updated_at"])
    return TriggerChange(trigger)


def rotate_secret(job: Job, kind: TriggerKind) -> TriggerChange:
    """Replace the secret. Requests signed with the old one are refused."""
    trigger = JobTrigger.objects.filter(job=job, kind=kind).first()
    if trigger is None:
        raise TriggerError("This trigger is not set up.")
    secret = new_secret()
    trigger.secret = secret
    trigger.secret_rotated_at = timezone.now()
    trigger.save(update_fields=["secret", "secret_rotated_at", "updated_at"])
    return TriggerChange(trigger, secret)


def record(
    trigger: JobTrigger, outcome: str, reason: str = "", run: Run | None = None
) -> None:
    """Keep the event, and only the newest few for each trigger."""
    with transaction.atomic():
        TriggerEvent.objects.create(
            trigger=trigger, outcome=outcome, reason=reason[:500], run=run
        )
        stale = trigger.events.order_by("-received_at", "-pk").values_list(
            "pk", flat=True
        )[settings.TRIGGER_EVENTS_KEPT :]
        TriggerEvent.objects.filter(pk__in=list(stale)).delete()


def refuse(trigger: JobTrigger, status: int, reason: str) -> Received:
    record(trigger, TriggerEvent.Outcome.REFUSED, reason)
    return Received(status, reason)


def not_ready_reason(error: RunNotReady) -> str:
    return "The job is not ready: " + " ".join(error.problems)


def receive(trigger: JobTrigger, headers: Mapping[str, str], body: bytes) -> Received:
    """Check one request and start a run if everything allows it."""
    verdict = verify(
        headers,
        body,
        trigger.secret,
        now=time.time(),
        max_age_seconds=settings.TRIGGER_SIGNATURE_MAX_AGE_SECONDS,
    )
    if not verdict.ok:
        return refuse(trigger, 401, verdict.reason)
    if not trigger.enabled:
        return refuse(trigger, 409, "This trigger is turned off.")
    job = trigger.job
    if job.repository.removed_at is not None:
        return refuse(trigger, 409, "The job's repository was removed.")
    if not declared(job, TriggerKind(trigger.kind)):
        return refuse(
            trigger,
            409,
            f"The job's configuration no longer names a {trigger.kind} trigger.",
        )
    try:
        run = start_run(job, RUN_STARTS[TriggerKind(trigger.kind)], body)
    except RunInProgress as error:
        return refuse(
            trigger, 409, f"Run #{error.run.pk} of this job has not finished."
        )
    except RunNotReady as error:
        return refuse(trigger, 409, not_ready_reason(error))
    except RunRefused as error:
        return refuse(trigger, 409, str(error))
    record(trigger, TriggerEvent.Outcome.STARTED, run=run)
    return Received(202, "Run started.", run)
