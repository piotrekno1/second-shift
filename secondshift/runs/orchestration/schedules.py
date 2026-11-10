"""DBOS schedules: one per enabled job with a cron schedule."""

from dbos import DBOSClient
from django.conf import settings

from secondshift.config.constants import SCHEDULED_JOB_WORKFLOW
from secondshift.jobs.dao import schedulable_jobs
from secondshift.runs.orchestration.dbos_runtime import new_dbos_client

SCHEDULE_PREFIX = "secondshift-job-"


def schedule_name(job_id: int) -> str:
    return f"{SCHEDULE_PREFIX}{job_id}"


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
        for job in schedulable_jobs()
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
