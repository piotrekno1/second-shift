"""The run limits of one job. See secondshift.workspaces.limits."""

from secondshift.jobs.models import Job
from secondshift.workspaces.limits import (
    LimitView,
    RunLimit,
    capped,
    limit_views,
    workspace_value,
)


def job_value(job: Job, limit: RunLimit) -> int:
    chosen = getattr(job, limit)
    if chosen is not None:
        return capped(limit, chosen)
    return workspace_value(job.workspace, limit)


def effective_limits(job: Job) -> dict[str, int]:
    """The limits a run of this job gets, for its execution plan."""
    return {str(limit): job_value(job, limit) for limit in RunLimit}


def job_limit_views(job: Job) -> list[LimitView]:
    return limit_views(job, lambda limit: workspace_value(job.workspace, limit))
