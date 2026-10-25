from typing import Literal

from dbos import DBOSClient
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from secondshift.gateway.models import RunToken
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.orchestration.dbos_runtime import new_dbos_client

ACTIVE_RUN_STATUSES = (Run.Status.QUEUED, Run.Status.RUNNING)
FAILED_WORKFLOW_STATUSES = {"ERROR", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"}
TERMINAL_WORKFLOW_STATUSES = {*FAILED_WORKFLOW_STATUSES, "CANCELLED", "SUCCESS"}


@transaction.atomic
def mark_run_terminal(
    run_id: int,
    status: Literal["failed", "canceled"],
    summary: str,
    *,
    workflow_status: str,
    metadata: dict[str, object] | None = None,
) -> bool:
    run = (
        Run.objects.select_for_update()
        .filter(pk=run_id, status__in=ACTIVE_RUN_STATUSES)
        .first()
    )
    if run is None:
        return False
    RunEvent.objects.create(
        run=run,
        level="error" if status == Run.Status.FAILED else "info",
        message=summary,
        metadata={**(metadata or {}), "workflow_status": workflow_status},
    )
    run.status = status
    run.summary = summary
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "summary", "finished_at"])
    RunToken.objects.filter(run=run, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )
    return True


def reconcile_terminal_workflows(client: DBOSClient | None = None) -> int:
    pending_runs = list(
        Run.objects.filter(
            status__in=ACTIVE_RUN_STATUSES,
        )
        .exclude(dbos_workflow_id="")
        .values_list("pk", "dbos_workflow_id")[:500]
    )
    if not pending_runs:
        return 0

    owns_client = client is None
    client = client or new_dbos_client()
    try:
        workflows = client.list_workflows(
            workflow_ids=[workflow_id for _, workflow_id in pending_runs],
            application_name=settings.DBOS_APPLICATION_NAME,
            load_input=False,
            load_output=False,
        )
    finally:
        if owns_client:
            client.destroy()

    run_ids = {workflow_id: run_id for run_id, workflow_id in pending_runs}
    reconciled = 0
    for workflow in workflows:
        if workflow.status not in TERMINAL_WORKFLOW_STATUSES:
            continue
        run_id = run_ids.get(workflow.workflow_id)
        if run_id is None:
            continue
        if workflow.status == "CANCELLED":
            status = Run.Status.CANCELED
            summary = "The durable workflow was canceled before the run completed."
        elif workflow.status == "SUCCESS":
            status = Run.Status.FAILED
            summary = "The workflow completed without persisting a terminal run result."
        else:
            status = Run.Status.FAILED
            summary = "The durable workflow failed before the run completed."
        reconciled += int(
            mark_run_terminal(
                run_id,
                status,
                summary,
                workflow_status=workflow.status,
            )
        )
    return reconciled
