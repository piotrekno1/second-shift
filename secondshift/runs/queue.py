"""Changes a person may make to a task that has not started."""

from django.db import transaction

from secondshift.repositories.dao import lock_repository
from secondshift.runs.dao import add_event, lock_run
from secondshift.runs.models import Run
from secondshift.runs.services import RunRefused
from secondshift.runs.transitions import finish_run


@transaction.atomic
def cancel_queued_task(run: Run) -> Run:
    if run.job_id is not None:
        raise RunRefused("Only queued ad hoc tasks can be canceled here.")
    lock_repository(run.task.repository_id)
    run = lock_run(run.pk, "task__repository")
    if run.status == Run.Status.CANCELED:
        return run
    if run.status != Run.Status.QUEUED:
        raise RunRefused("This task has already started or finished.")
    summary = "Canceled before execution."
    finish_run(run.pk, Run.Status.CANCELED, summary, only_from={Run.Status.QUEUED})
    run.refresh_from_db(fields=["status", "summary", "finished_at"])
    add_event(run.pk, summary)
    return run
