"""The run time limit.

The limit counts from when a run starts, so time spent in the queue is free.
A run is checked before each model request and each tool call. A call that is
already in progress finishes, bounded by its own timeout.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from secondshift.core.workspace_settings import duration_label

TIMEOUT_HEADLINE = "Timed out"


class RunTimedOut(RuntimeError):
    pass


def run_deadline(started_at: datetime | None) -> datetime:
    start = started_at or timezone.now()
    return start + timedelta(seconds=settings.RUN_TIMEOUT_SECONDS)


def check_deadline(deadline: datetime) -> None:
    if timezone.now() >= deadline:
        raise RunTimedOut(timeout_reason())


def timeout_reason() -> str:
    return (
        f"Stopped after {duration_label(settings.RUN_TIMEOUT_SECONDS)}, "
        "the run time limit. Set SECONDSHIFT_RUN_TIMEOUT_SECONDS to allow "
        "longer runs."
    )


def timeout_summary() -> str:
    return f"{TIMEOUT_HEADLINE}: {timeout_reason()}"
