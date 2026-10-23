from dbos import EnqueueOptions
from django.conf import settings
from django.utils import timezone

from secondshift.automations.dbos_runtime import new_dbos_client
from secondshift.automations.models import Run, RunEvent
from secondshift.automations.workflows import EXECUTE_RUN_WORKFLOW


def enqueue_run(run: Run) -> str:
    workflow_id = f"secondshift-run-{run.pk}"
    options: EnqueueOptions = {
        "workflow_name": EXECUTE_RUN_WORKFLOW,
        "queue_name": settings.DBOS_QUEUE_NAME,
        "workflow_id": workflow_id,
        "application_name": settings.DBOS_APPLICATION_NAME,
    }
    client = new_dbos_client()
    try:
        handle = client.enqueue(options, run.pk)
        workflow_id = handle.get_workflow_id()
    except Exception:
        message = "The durable workflow could not be enqueued."
        RunEvent.objects.create(run=run, level="error", message=message)
        run.status = Run.Status.FAILED
        run.summary = message
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "summary", "finished_at"])
        raise
    finally:
        client.destroy()

    run.dbos_workflow_id = workflow_id
    run.save(update_fields=["dbos_workflow_id"])
    return workflow_id
