"""Create execution records from job policy for manual and scheduled triggers."""

from datetime import datetime
from typing import Any

from secondshift.jobs.models import Job
from secondshift.jobs.planning import build_execution_plan
from secondshift.runs.artifacts import job_input_snapshot
from secondshift.runs.models import Run


def create_run(
    job: Job,
    scheduled_for: datetime,
    *,
    execution_plan: dict[str, Any] | None = None,
    reuse_existing: bool = False,
) -> Run:
    defaults = {
        "commit_sha": job.repository.config_sha,
        "input_snapshot": job_input_snapshot(job),
        "execution_plan": (
            build_execution_plan(job) if execution_plan is None else execution_plan
        ),
    }
    if reuse_existing:
        return Run.objects.get_or_create(
            job=job, scheduled_for=scheduled_for, defaults=defaults
        )[0]
    return Run.objects.create(job=job, scheduled_for=scheduled_for, **defaults)
