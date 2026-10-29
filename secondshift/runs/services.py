"""Create execution records from job policy for manual and scheduled triggers."""

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from secondshift.jobs.models import Job
from secondshift.jobs.planning import build_execution_plan
from secondshift.runs.artifacts import job_input_snapshot
from secondshift.runs.history import previous_run_ids
from secondshift.runs.models import Run
from secondshift.runs.orchestration.dispatch import enqueue_run


class RunRefused(Exception):
    pass


class RunInProgress(RunRefused):
    """The job already has a run that has not finished."""

    def __init__(self, run: Run) -> None:
        super().__init__("This job already has a queued or running run.")
        self.run = run


def create_run(
    job: Job,
    scheduled_for: datetime,
    *,
    execution_plan: dict[str, Any] | None = None,
    reuse_existing: bool = False,
) -> Run:
    defaults = {
        "commit_sha": job.repository.config_sha,
        "input_snapshot": job_input_snapshot(job)
        | {"history_run_ids": previous_run_ids(job)},
        "execution_plan": (
            build_execution_plan(job) if execution_plan is None else execution_plan
        ),
    }
    if reuse_existing:
        return Run.objects.get_or_create(
            job=job, scheduled_for=scheduled_for, defaults=defaults
        )[0]
    return Run.objects.create(job=job, scheduled_for=scheduled_for, **defaults)


def start_manual_run(job: Job) -> Run:
    """Run now. Refuses while another run of the job has not finished.

    One run at a time per job also stops a client stuck in a loop from
    queuing runs faster than they finish.
    """
    # A manual run does not need the schedule. It still needs the repository.
    if not job.repository.enabled:
        raise RunRefused("Enable the repository before running its jobs.")
    with transaction.atomic():
        # Two starts at the same moment wait here, so only one creates a run.
        Job.objects.select_for_update().filter(pk=job.pk).first()
        active = job.runs.filter(status__in=Run.ACTIVE_STATUSES).order_by("-pk").first()
        if active is not None:
            raise RunInProgress(active)
        execution_plan = build_execution_plan(job)
        if not execution_plan["ready"]:
            raise RunRefused(
                "Connect a default model and approve all required capabilities first."
            )
        run = create_run(job, timezone.now(), execution_plan=execution_plan)
    enqueue_run(run)
    return run
