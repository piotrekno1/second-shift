"""Changes a person may make to a task that has not started."""

from django.db import transaction
from django.utils import timezone

from secondshift.repositories.models import Repository
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.services import RunRefused


@transaction.atomic
def cancel_queued_task(run: Run) -> Run:
    if run.job_id is not None:
        raise RunRefused("Only queued ad hoc tasks can be canceled here.")
    Repository.objects.select_for_update().get(pk=run.task.repository_id)
    run = (
        Run.objects.select_for_update()
        .select_related("task__repository")
        .get(pk=run.pk)
    )
    if run.status == Run.Status.CANCELED:
        return run
    if run.status != Run.Status.QUEUED:
        raise RunRefused("This task has already started or finished.")
    run.status = Run.Status.CANCELED
    run.summary = "Canceled before execution."
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "summary", "finished_at"])
    RunEvent.objects.create(run=run, message=run.summary)
    return run
