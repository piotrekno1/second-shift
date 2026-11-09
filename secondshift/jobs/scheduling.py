from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from django.utils import timezone

from secondshift.jobs.dao import save_next_runs
from secondshift.jobs.models import Job


def next_occurrence(job: Job, after: datetime | None = None) -> datetime:
    base = after or timezone.now()
    local_base = base.astimezone(ZoneInfo(job.timezone))
    next_local = croniter(job.cron, local_base).get_next(datetime)
    return next_local.astimezone(ZoneInfo("UTC"))


def restart_schedules(jobs: list[Job]) -> None:
    """Set the next run from now for jobs whose schedule was off.

    A job keeps its old next run while it is off. That time is in the past
    when the job turns back on.
    """
    for job in jobs:
        job.next_run_at = next_occurrence(job) if job.cron else None
    save_next_runs(jobs)
