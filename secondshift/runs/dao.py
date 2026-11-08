"""Named queries on runs.

Other apps read runs through these helpers, not Run.objects or job.runs.
"""

from datetime import datetime

from django.db.models import Q, QuerySet

from secondshift.jobs.models import Job
from secondshift.runs.models import RUN_ENQUEUE_FAILED, Run

# Runs the queue could not hand off. They never started, so they do not count
# as earlier or recent runs.
ENQUEUE_FAILED = Q(status=Run.Status.FAILED, summary=RUN_ENQUEUE_FAILED)


def active_runs() -> QuerySet[Run]:
    """Runs that have not finished yet."""
    return Run.objects.filter(status__in=Run.ACTIVE_STATUSES)


def latest_active_run(job: Job) -> Run | None:
    """The job's newest unfinished run, if it has one."""
    return active_runs().filter(job=job).order_by("-pk").first()


def active_tasks(repository_id: int) -> QuerySet[Run]:
    """Unfinished ad hoc task runs in the repository."""
    return active_runs().filter(job__isnull=True, task__repository_id=repository_id)


def run_for_repeat_key(job: Job, key: str, finished_since: datetime) -> Run | None:
    """The job's newest run for this event that is active or finished since then."""
    return (
        Run.objects.filter(job=job, input_snapshot__repeat_key=key)
        .filter(Q(status__in=Run.ACTIVE_STATUSES) | Q(finished_at__gte=finished_since))
        .exclude(ENQUEUE_FAILED)
        .order_by("-pk")
        .first()
    )


def started_run_count(job: Job, started_by: str, since: datetime) -> int:
    """How many runs this source started for the job since then."""
    return (
        Run.objects.filter(job=job, started_by=started_by, created_at__gte=since)
        .exclude(ENQUEUE_FAILED)
        .count()
    )
