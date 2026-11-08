"""The run limits of one job. See secondshift.workspaces.limits."""

from collections.abc import Mapping
from typing import Any

from secondshift.jobs.models import Job
from secondshift.workspaces.limits import (
    LimitView,
    RunLimit,
    capped,
    limit_views,
    server_default,
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


def plan_limit(plan: Mapping[str, Any], limit: RunLimit) -> int:
    """A limit frozen into an execution plan.

    Plans made before a limit was frozen fall back to the server default.
    """
    value = ((plan.get("agent") or {}).get("limits") or {}).get(str(limit))
    return int(value) if isinstance(value, int) else server_default(limit)
