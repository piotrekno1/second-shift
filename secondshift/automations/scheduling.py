from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from dbos import DBOSClient
from django.conf import settings
from django.utils import timezone

from secondshift.automations.dbos_runtime import new_dbos_client
from secondshift.automations.models import Job

SCHEDULE_PREFIX = "secondshift-job-"
SCHEDULED_JOB_WORKFLOW = "secondshift.scheduled_job"


def next_occurrence(job: Job, after: datetime | None = None) -> datetime:
    base = after or timezone.now()
    local_base = base.astimezone(ZoneInfo(job.timezone))
    next_local = croniter(job.cron, local_base).get_next(datetime)
    return next_local.astimezone(ZoneInfo("UTC"))


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
        for job in Job.objects.filter(enabled=True, repository__enabled=True).order_by(
            "pk"
        )
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
            existing_name = (
                schedule["schedule_name"]
                if isinstance(schedule, dict)
                else schedule.schedule_name
            )
            if existing_name not in desired_names:
                client.delete_schedule(existing_name)
    finally:
        if owns_client:
            client.destroy()
    return len(schedules)
