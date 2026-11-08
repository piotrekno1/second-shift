"""Turn job triggers on and off, and start runs from the requests they receive.

The repository names the trigger. Turning it on creates its URL and secret.
"""

import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from secondshift.config.constants import RunStart, TriggerKind
from secondshift.jobs.models import JOB_REMOVED, Job
from secondshift.runs.dao import run_for_repeat_key, started_run_count
from secondshift.runs.models import Run
from secondshift.runs.orchestration.dispatch import RunEnqueueFailed
from secondshift.runs.services import RunInProgress, RunNotReady, RunRefused, start_run
from secondshift.triggers.dao import (
    add_event,
    create_trigger,
    has_recent_event,
    job_trigger,
)
from secondshift.triggers.filters import (
    parse_body,
    repeat_key,
    repeat_window_seconds,
    skip_reason,
)
from secondshift.triggers.models import JobTrigger, TriggerEvent
from secondshift.triggers.verification import verify

# A failed sign-in is recorded at most this often, so guessing cannot flood
# the log.
UNAUTHORIZED_RECORD_SECONDS = 60

# Secret scanners can match trigger secrets by this prefix.
SECRET_PREFIX = "sst_"

RUN_STARTS = {TriggerKind.WEBHOOK: RunStart.WEBHOOK}
ENQUEUE_RETRY_SECONDS = 60


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
    retry_after: int | None = None


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def declared(job: Job, kind: TriggerKind) -> bool:
    return kind in job.triggers


def set_trigger_enabled(job: Job, kind: TriggerKind, enabled: bool) -> TriggerChange:
    """Turn a trigger on or off. The first time on creates its URL and secret."""
    trigger = job_trigger(job, kind)
    if enabled and not declared(job, kind):
        raise TriggerError(
            f"The job's configuration does not name a {kind} trigger. "
            f"Add triggers: [{kind}] to the job in .secondshift.yaml."
        )
    if trigger is None:
        if not enabled:
            raise TriggerError("This trigger is not set up.")
        secret = new_secret()
        trigger = create_trigger(job, kind, secret, timezone.now())
        return TriggerChange(trigger, secret)
    trigger.enabled = enabled
    trigger.save(update_fields=["enabled", "updated_at"])
    return TriggerChange(trigger)


def rotate_secret(job: Job, kind: TriggerKind) -> TriggerChange:
    """Replace the secret. Requests signed with the old one are refused."""
    trigger = job_trigger(job, kind)
    if trigger is None:
        raise TriggerError("This trigger is not set up.")
    secret = new_secret()
    trigger.secret = secret
    trigger.secret_rotated_at = timezone.now()
    trigger.save(update_fields=["secret", "secret_rotated_at", "updated_at"])
    return TriggerChange(trigger, secret)


def set_runs_per_hour(job: Job, kind: TriggerKind, value: int | None) -> JobTrigger:
    """Set the trigger's hourly cap. None goes back to the server default."""
    trigger = job_trigger(job, kind)
    if trigger is None:
        raise TriggerError("This trigger is not set up.")
    ceiling = settings.TRIGGER_MAX_RUNS_PER_HOUR_CEILING
    if value is not None and value > ceiling:
        raise TriggerError(f"This server allows at most {ceiling} runs per hour.")
    trigger.max_runs_per_hour = value
    trigger.save(update_fields=["max_runs_per_hour", "updated_at"])
    return trigger


def record(
    trigger: JobTrigger, outcome: str, reason: str = "", run: Run | None = None
) -> None:
    """Keep the event, and only the newest few of each outcome for each trigger.

    Kept per outcome, so a flood of refusals never pushes out started runs.
    """
    add_event(trigger, outcome, reason[:500], run, settings.TRIGGER_EVENTS_KEPT)


def refuse(trigger: JobTrigger, status: int, reason: str) -> Received:
    record(trigger, TriggerEvent.Outcome.REFUSED, reason)
    return Received(status, reason)


def ignore(trigger: JobTrigger, reason: str) -> Received:
    """Answer 2xx, so the sender does not retry a request the job skips."""
    record(trigger, TriggerEvent.Outcome.IGNORED, reason)
    return Received(200, reason)


def refuse_unauthorized(trigger: JobTrigger, reason: str) -> Received:
    since = timezone.now() - timedelta(seconds=UNAUTHORIZED_RECORD_SECONDS)
    if not has_recent_event(trigger, TriggerEvent.Outcome.REFUSED, reason, since):
        record(trigger, TriggerEvent.Outcome.REFUSED, reason)
    return Received(401, reason)


def runs_per_hour(trigger: JobTrigger | None) -> int:
    """The owner's cap or the server default, never above the server ceiling."""
    chosen = (trigger and trigger.max_runs_per_hour) or (
        settings.TRIGGER_MAX_RUNS_PER_HOUR
    )
    return min(chosen, settings.TRIGGER_MAX_RUNS_PER_HOUR_CEILING)


def earlier_run(job: Job, key: str, webhook: dict[str, Any]) -> Run | None:
    """A run for the same event that is active or finished within the window."""
    since = timezone.now() - timedelta(seconds=repeat_window_seconds(webhook))
    return run_for_repeat_key(job, key, since)


def repeat_reason(run: Run) -> str:
    return f"Repeat of the event that started run #{run.pk}."


def not_ready_reason(error: RunNotReady) -> str:
    return "The job is not ready: " + " ".join(error.problems)


def refusal(trigger: JobTrigger) -> Received | None:
    """Why the trigger itself cannot start a run, before the body is read."""
    if not trigger.enabled:
        return refuse(trigger, 409, "This trigger is turned off.")
    job = trigger.job
    if job.repository.removed_at is not None:
        return refuse(trigger, 409, "The job's repository was removed.")
    if job.removed_at is not None:
        return refuse(trigger, 409, JOB_REMOVED)
    if not declared(job, TriggerKind(trigger.kind)):
        return refuse(
            trigger,
            409,
            f"The job's configuration no longer names a {trigger.kind} trigger.",
        )
    return None


def hourly_refusal(trigger: JobTrigger) -> Received | None:
    limit = runs_per_hour(trigger)
    started = started_run_count(
        trigger.job,
        RUN_STARTS[TriggerKind(trigger.kind)],
        timezone.now() - timedelta(hours=1),
    )
    if started < limit:
        return None
    runs = "run" if limit == 1 else "runs"
    return refuse(
        trigger, 429, f"This trigger already started {limit} {runs} in the last hour."
    )


def start(trigger: JobTrigger, body: bytes, key: str | None) -> Received:
    try:
        run = start_run(trigger.job, RUN_STARTS[TriggerKind(trigger.kind)], body, key)
    except RunInProgress as error:
        if key is not None and error.run.input_snapshot.get("repeat_key") == key:
            return ignore(trigger, repeat_reason(error.run))
        return refuse(
            trigger, 409, f"Run #{error.run.pk} of this job has not finished."
        )
    except RunNotReady as error:
        return refuse(trigger, 409, not_ready_reason(error))
    except RunRefused as error:
        return refuse(trigger, 409, str(error))
    except RunEnqueueFailed as error:
        reason = "The run could not be queued."
        record(trigger, TriggerEvent.Outcome.REFUSED, reason, run=error.run)
        return Received(503, reason, error.run, retry_after=ENQUEUE_RETRY_SECONDS)
    record(trigger, TriggerEvent.Outcome.STARTED, run=run)
    return Received(202, "Run started.", run)


def receive(trigger: JobTrigger, headers: Mapping[str, str], body: bytes) -> Received:
    """Check one request and start a run if everything allows it.

    In order: the secret, the trigger, the job's webhook settings (skip and
    repeats), the hourly cap, then the job itself.
    """
    verdict = verify(
        headers,
        body,
        trigger.secret,
        now=time.time(),
        max_age_seconds=settings.TRIGGER_SIGNATURE_MAX_AGE_SECONDS,
    )
    if not verdict.ok:
        return refuse_unauthorized(trigger, verdict.reason)
    if refused := refusal(trigger):
        return refused
    webhook = trigger.job.webhook or {}
    data = parse_body(body) if webhook else None
    if reason := skip_reason(data, webhook.get("skip_when") or {}):
        return ignore(trigger, reason)
    key = repeat_key(data, webhook.get("repeat_key"))
    if key is not None and (earlier := earlier_run(trigger.job, key, webhook)):
        return ignore(trigger, repeat_reason(earlier))
    if refused := hourly_refusal(trigger):
        return refused
    return start(trigger, body, key)
