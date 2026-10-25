"""The run time limit.

The limit counts from when a run starts, so time spent in the queue is free.
A run is checked before each model request and each tool call. A call that is
already in progress finishes, bounded by its own timeout.
"""

from datetime import datetime, timedelta

from django.utils import timezone

from secondshift.automations.limits import RunLimit, plan_limit, raise_hint
from secondshift.automations.models import Run
from secondshift.core.workspace_settings import duration_label

TIMEOUT_HEADLINE = "Timed out"


class RunTimedOut(RuntimeError):
    pass


def run_timeout_seconds(run: Run) -> int:
    return plan_limit(run.execution_plan, RunLimit.TIMEOUT_SECONDS)


def run_deadline(run: Run) -> datetime:
    start = run.started_at or timezone.now()
    return start + timedelta(seconds=run_timeout_seconds(run))


def check_deadline(deadline: datetime) -> None:
    if timezone.now() >= deadline:
        raise RunTimedOut("The run reached its time limit.")


def timeout_reason(seconds: int) -> str:
    return (
        f"Stopped after {duration_label(seconds)}, the run's time limit. "
        f"{raise_hint(RunLimit.TIMEOUT_SECONDS, seconds)}"
    )


def timeout_summary(seconds: int) -> str:
    return f"{TIMEOUT_HEADLINE}: {timeout_reason(seconds)}"
