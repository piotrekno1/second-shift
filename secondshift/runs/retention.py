from datetime import timedelta

from dbos import DBOSClient
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from secondshift.runs.models import Run, Task
from secondshift.runs.orchestration.dbos_runtime import new_dbos_client

FINISHED_STATUSES = (Run.Status.SUCCEEDED, Run.Status.FAILED, Run.Status.CANCELED)


def prune_runs(days: int | None = None, client: DBOSClient | None = None) -> int:
    """Delete finished runs older than the retention window and their DBOS
    workflow records; the post_delete signal removes stored output.
    Returns the number of runs pruned."""
    days = settings.RUN_RETENTION_DAYS if days is None else days
    if days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    runs = list(
        Run.objects.filter(
            status__in=FINISHED_STATUSES, finished_at__lt=cutoff
        ).select_related("job")
    )
    if not runs:
        return 0

    workflow_ids = [run.dbos_workflow_id for run in runs if run.dbos_workflow_id]
    if workflow_ids:
        owns_client = client is None
        client = client or new_dbos_client()
        try:
            client.delete_workflows(workflow_ids, delete_children=True)
        finally:
            if owns_client:
                client.destroy()

    with transaction.atomic():
        task_ids = {run.task_id for run in runs if run.task_id is not None}
        Run.objects.filter(pk__in=[run.pk for run in runs]).delete()
        # A task follows its attempts' retention. Keep it if another attempt
        # still exists, and never sweep unrelated assignments here.
        Task.objects.filter(pk__in=task_ids, runs__isnull=True).delete()
    return len(runs)
