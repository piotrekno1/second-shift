import logging

from dbos import EnqueueOptions
from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from secondshift.config.constants import EXECUTE_RUN_WORKFLOW
from secondshift.repositories.dao import lock_repository
from secondshift.runs.dao import active_runs, active_tasks
from secondshift.runs.models import RUN_ENQUEUE_FAILED, Run, RunEvent
from secondshift.runs.orchestration.admission import task_admission_problem
from secondshift.runs.orchestration.dbos_runtime import new_dbos_client
from secondshift.runs.orchestration.reconciliation import mark_run_terminal
from secondshift.runs.transitions import finish_run

logger = logging.getLogger(__name__)


class RunEnqueueFailed(Exception):
    """The workflow could not be queued and the run was marked failed."""

    def __init__(self, run: Run) -> None:
        super().__init__(RUN_ENQUEUE_FAILED)
        self.run = run


def enqueue_run(run: Run) -> str:
    if run.job_id is None:
        return enqueue_task(run)
    return enqueue_workflow(run)


def enqueue_workflow(run: Run) -> str:
    workflow_id = f"secondshift-run-{run.pk}"
    options: EnqueueOptions = {
        "workflow_name": EXECUTE_RUN_WORKFLOW,
        "queue_name": settings.DBOS_QUEUE_NAME,
        "workflow_id": workflow_id,
        "application_name": settings.DBOS_APPLICATION_NAME,
    }
    client = None
    try:
        client = new_dbos_client()
        handle = client.enqueue(options, run.pk)
        workflow_id = handle.get_workflow_id()
    except Exception as error:
        if run.job_id is None:
            # The Run is the durable outbox. Retry with the same workflow ID,
            # including after a crash between DBOS accepting and our save.
            RunEvent.objects.get_or_create(
                run=run,
                message="Waiting for the execution queue. The worker will retry automatically.",
                defaults={"level": "warning"},
            )
            return ""
        RunEvent.objects.create(run=run, level="error", message=RUN_ENQUEUE_FAILED)
        failed = finish_run(
            run.pk, Run.Status.FAILED, RUN_ENQUEUE_FAILED, only_from={Run.Status.QUEUED}
        )
        if failed is not None:
            raise RunEnqueueFailed(failed) from error
        raise
    finally:
        if client is not None:
            try:
                client.destroy()
            except Exception:
                logger.warning(
                    "Could not close execution queue client for run %s", run.pk
                )

    run.dbos_workflow_id = workflow_id
    run.save(update_fields=["dbos_workflow_id"])
    return workflow_id


@transaction.atomic
def enqueue_task(run: Run) -> str:
    """Hand the oldest eligible task to DBOS, once per repository at a time.

    The repository lock covers the external handoff and its acknowledgement.
    It serializes submissions, worker retries and queued cancellations.
    """
    repository = lock_repository(run.task.repository_id)
    tasks = Run.objects.filter(job__isnull=True, task__repository=repository)
    run = tasks.select_related("task").get(pk=run.pk)
    if run.status != Run.Status.QUEUED:
        return ""
    if run.dbos_workflow_id:
        return run.dbos_workflow_id
    if run.scheduled_for > timezone.now():
        return ""
    if problem := task_admission_problem(run, repository):
        mark_run_terminal(
            run.pk, Run.Status.FAILED, problem, workflow_status="NOT_ADMITTED"
        )
        return ""
    if active_tasks(repository.pk).exclude(dbos_workflow_id="").exists():
        return ""
    first = (
        tasks.filter(
            status=Run.Status.QUEUED,
            scheduled_for__lte=timezone.now(),
            dbos_workflow_id="",
        )
        .order_by("scheduled_for", "created_at", "pk")
        .first()
    )
    if first is None or first.pk != run.pk:
        return ""
    return enqueue_workflow(run)


def dispatch_waiting_tasks() -> int:
    """Recover committed submissions and advance the queue on each worker pass."""
    blocked = (
        active_runs()
        .filter(job__isnull=True, task__repository_id=OuterRef("task__repository_id"))
        .exclude(dbos_workflow_id="")
    )
    waiting = list(
        Run.objects.filter(
            job__isnull=True,
            status=Run.Status.QUEUED,
            dbos_workflow_id="",
            scheduled_for__lte=timezone.now(),
        )
        .filter(~Exists(blocked))
        .select_related("task")
        .order_by("scheduled_for", "created_at", "pk")[:500]
    )
    return sum(bool(enqueue_task(run)) for run in waiting)
