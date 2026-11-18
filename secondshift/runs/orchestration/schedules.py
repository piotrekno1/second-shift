"""DBOS schedules: one per enabled job with a cron schedule."""

import logging

from dbos import DBOSClient
from django.conf import settings

from secondshift.jobs.dao import schedulable_jobs
from secondshift.jobs.manifest import schedule_problem
from secondshift.jobs.models import Job
from secondshift.runs.constants import SCHEDULED_JOB_WORKFLOW
from secondshift.runs.orchestration.dbos_runtime import new_dbos_client

SCHEDULE_PREFIX = "secondshift-job-"

logger = logging.getLogger(__name__)


def schedule_name(job_id: int) -> str:
    return f"{SCHEDULE_PREFIX}{job_id}"


def runnable_jobs() -> list[Job]:
    """Jobs whose schedule DBOS accepts.

    A job saved before manifests checked this may hold one it rejects. DBOS
    applies schedules as one batch, so that job is left out, not the batch.
    """
    jobs = []
    for job in schedulable_jobs():
        if problem := schedule_problem(job.cron):
            logger.warning("not scheduling job %s: %s", job.pk, problem)
            continue
        jobs.append(job)
    return jobs


def desired_schedules() -> list[dict[str, object]]:
    return [
        {
            "schedule_name": schedule_name(job.pk),
            "workflow_name": SCHEDULED_JOB_WORKFLOW,
            "workflow_class_name": None,
            "schedule": job.cron,
            "context": {"job_id": job.pk},
            "automatic_backfill": False,
            "cron_timezone": job.timezone,
            "queue_name": settings.DBOS_QUEUE_NAME,
            "application_name": settings.DBOS_APPLICATION_NAME,
        }
        for job in runnable_jobs()
    ]


def synchronize_schedules(client: DBOSClient | None = None) -> int:
    schedules = desired_schedules()
    desired_names = {str(item["schedule_name"]) for item in schedules}
    owns_client = client is None
    client = client or new_dbos_client()
    try:
        client.apply_schedules(schedules)  # type: ignore[arg-type]
        existing = client.list_schedules(
            schedule_name_prefix=SCHEDULE_PREFIX,
            application_name=settings.DBOS_APPLICATION_NAME,
        )
        for schedule in existing:
            if schedule["schedule_name"] not in desired_names:
                client.delete_schedule(schedule["schedule_name"])
    finally:
        if owns_client:
            client.destroy()
    return len(schedules)
