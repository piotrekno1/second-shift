"""Changes a person may make to a task that has not started."""

from django.db import transaction

from secondshift.repositories.dao import lock_repository
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.services import RunRefused
from secondshift.runs.transitions import finish_run


@transaction.atomic
def cancel_queued_task(run: Run) -> Run:
    if run.job_id is not None:
        raise RunRefused("Only queued ad hoc tasks can be canceled here.")
    lock_repository(run.task.repository_id)
    run = (
        Run.objects.select_for_update()
        .select_related("task__repository")
        .get(pk=run.pk)
    )
    if run.status == Run.Status.CANCELED:
        return run
    if run.status != Run.Status.QUEUED:
        raise RunRefused("This task has already started or finished.")
    summary = "Canceled before execution."
    finish_run(run.pk, Run.Status.CANCELED, summary, only_from={Run.Status.QUEUED})
    run.refresh_from_db(fields=["status", "summary", "finished_at"])
    RunEvent.objects.create(run=run, message=summary)
    return run
