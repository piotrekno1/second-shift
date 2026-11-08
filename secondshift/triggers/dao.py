"""Queries and writes on job triggers and the requests they receive."""

from datetime import datetime

from django.db import transaction

from secondshift.jobs.models import Job
from secondshift.runs.models import Run
from secondshift.triggers.models import JobTrigger, TriggerEvent


def trigger_for_url(public_id: str) -> JobTrigger | None:
    """The trigger behind a public webhook URL, with its job and repository."""
    return (
        JobTrigger.objects.select_related("job__repository")
        .filter(public_id=public_id)
        .first()
    )


def job_trigger(job: Job, kind: str) -> JobTrigger | None:
    """The job's trigger of this kind, if it was ever set up."""
    return JobTrigger.objects.filter(job=job, kind=kind).first()


def create_trigger(
    job: Job, kind: str, secret: str, secret_rotated_at: datetime
) -> JobTrigger:
    """Set up an enabled trigger with its first secret."""
    return JobTrigger.objects.create(
        job=job,
        kind=kind,
        secret=secret,
        enabled=True,
        secret_rotated_at=secret_rotated_at,
    )


def add_event(
    trigger: JobTrigger, outcome: str, reason: str, run: Run | None, keep: int
) -> None:
    """Record a request, keeping only the newest `keep` events of its outcome."""
    with transaction.atomic():
        TriggerEvent.objects.create(
            trigger=trigger, outcome=outcome, reason=reason, run=run
        )
        stale = (
            trigger.events.filter(outcome=outcome)
            .order_by("-received_at", "-pk")
            .values_list("pk", flat=True)[keep:]
        )
        TriggerEvent.objects.filter(pk__in=list(stale)).delete()


def has_recent_event(
    trigger: JobTrigger, outcome: str, reason: str, since: datetime
) -> bool:
    """Whether the trigger recorded this outcome and reason since then."""
    return trigger.events.filter(
        outcome=outcome, reason=reason, received_at__gte=since
    ).exists()
