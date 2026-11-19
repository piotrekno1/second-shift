from enum import StrEnum
from typing import Literal

from dbos import DBOSClient
from django.conf import settings
from django.db import transaction

from secondshift.runs.dao import active_workflows, add_event
from secondshift.runs.models import Run, RunFailure
from secondshift.runs.orchestration.dbos_runtime import new_dbos_client
from secondshift.runs.transitions import finish_run


class WorkflowStatus(StrEnum):
    """DBOS workflow statuses, plus the one recorded before DBOS starts a run."""

    ERROR = "ERROR"
    MAX_RECOVERY_ATTEMPTS_EXCEEDED = "MAX_RECOVERY_ATTEMPTS_EXCEEDED"
    CANCELLED = "CANCELLED"
    SUCCESS = "SUCCESS"
    NOT_ADMITTED = "NOT_ADMITTED"


TERMINAL_WORKFLOW_STATUSES = {
    WorkflowStatus.ERROR,
    WorkflowStatus.MAX_RECOVERY_ATTEMPTS_EXCEEDED,
    WorkflowStatus.CANCELLED,
    WorkflowStatus.SUCCESS,
}


@transaction.atomic
def mark_run_terminal(
    run_id: int,
    status: Literal["failed", "canceled"],
    summary: str,
    *,
    workflow_status: WorkflowStatus,
    metadata: dict[str, object] | None = None,
    failure: RunFailure = RunFailure.OTHER,
    reason: str = "",
) -> bool:
    run = finish_run(run_id, status, summary, failure=failure, reason=reason)
    if run is None:
        return False
    add_event(
        run.pk,
        summary,
        level="error" if status == Run.Status.FAILED else "info",
        metadata={**(metadata or {}), "workflow_status": workflow_status},
    )
    return True


RECONCILE_BATCH = 500


def reconcile_terminal_workflows(client: DBOSClient | None = None) -> int:
    """Finish runs whose workflow has ended, checking every active run.

    The runs go in batches by id, so a long backlog can't hide older ones.
    """
    reconciled = 0
    after_id = 0
    # Opened only once there is a run to check, and closed here.
    own_client = None
    try:
        while batch := active_workflows(after_id, RECONCILE_BATCH):
            if client is None:
                client = own_client = new_dbos_client()
            reconciled += reconcile_batch(client, batch)
            after_id = batch[-1][0]
    finally:
        if own_client is not None:
            own_client.destroy()
    return reconciled


def reconcile_batch(client: DBOSClient, pending_runs: list[tuple[int, str]]) -> int:
    workflows = client.list_workflows(
        workflow_ids=[workflow_id for _, workflow_id in pending_runs],
        application_name=settings.DBOS_APPLICATION_NAME,
        load_input=False,
        load_output=False,
    )
    run_ids = {workflow_id: run_id for run_id, workflow_id in pending_runs}
    reconciled = 0
    for workflow in workflows:
        if workflow.status not in TERMINAL_WORKFLOW_STATUSES:
            continue
        run_id = run_ids.get(workflow.workflow_id)
        if run_id is None:
            continue
        if workflow.status == WorkflowStatus.CANCELLED:
            status = Run.Status.CANCELED
            summary = "The durable workflow was canceled before the run completed."
        elif workflow.status == WorkflowStatus.SUCCESS:
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
