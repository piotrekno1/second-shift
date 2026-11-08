"""The only code that changes a run's status.

A run goes from queued to running, then succeeds, fails or is canceled. A
queued run can also fail or be canceled before it starts. A finished run
never changes again.
"""

from collections.abc import Collection, Mapping

from django.db import transaction
from django.utils import timezone

from secondshift.config.constants import RunSandbox
from secondshift.gateway.dao import revoke_run_tokens
from secondshift.runs.models import Run

# The statuses each status may move to. A finished status has no entry.
NEXT_STATUSES: dict[str, frozenset[str]] = {
    Run.Status.QUEUED: frozenset(
        {Run.Status.RUNNING, Run.Status.FAILED, Run.Status.CANCELED}
    ),
    Run.Status.RUNNING: frozenset(
        {Run.Status.SUCCEEDED, Run.Status.FAILED, Run.Status.CANCELED}
    ),
}


class TransitionRefused(Exception):
    pass


def can_move(current: str, new: str) -> bool:
    return new in NEXT_STATUSES.get(current, frozenset())


def start_run(run_id: int, workflow_id: str) -> bool:
    """Move a queued run to running. False when the run is not queued."""
    return bool(
        Run.objects.filter(pk=run_id, status=Run.Status.QUEUED).update(
            status=Run.Status.RUNNING,
            started_at=timezone.now(),
            sandbox_runtime=RunSandbox.PENDING,
            dbos_workflow_id=workflow_id,
        )
    )


@transaction.atomic
def finish_run(
    run_id: int,
    status: str,
    summary: str,
    *,
    only_from: Collection[str] = Run.ACTIVE_STATUSES,
    fields: Mapping[str, object] | None = None,
) -> Run | None:
    """Give a run its final status, summary and finish time, and revoke its tokens.

    Returns None and changes nothing when the run is not in `only_from`,
    usually because it already finished. `fields` are saved with the status.
    """
    if status in Run.ACTIVE_STATUSES:
        raise TransitionRefused(f"{status} is not a final status.")
    run = (
        Run.objects.select_for_update().filter(pk=run_id, status__in=only_from).first()
    )
    if run is None:
        return None
    if not can_move(run.status, status):
        raise TransitionRefused(f"A {run.status} run cannot become {status}.")
    extra = dict(fields or {})
    run.status = status
    run.summary = summary
    run.finished_at = timezone.now()
    for name, value in extra.items():
        setattr(run, name, value)
    run.save(update_fields=["status", "summary", "finished_at", *extra])
    revoke_run_tokens(run_id)
    return run
