"""Create runs from job policy, whether started by cron, by hand or by a trigger."""

from datetime import datetime

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from secondshift.config.constants import RunStart
from secondshift.jobs.dao import lock_job
from secondshift.jobs.models import JOB_REMOVED, Job
from secondshift.jobs.plan import ExecutionPlan
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.readiness import execution_problems
from secondshift.runs.artifacts import store_run_payload
from secondshift.runs.dao import (
    add_event,
    create_run_record,
    create_task,
    job_run_for_slot,
    latest_active_run,
)
from secondshift.runs.history import previous_run_ids
from secondshift.runs.issue_delivery import record_pending_issues
from secondshift.runs.models import Run
from secondshift.runs.orchestration.dispatch import enqueue_run
from secondshift.runs.publishing import record_pending_pull_request


class RunRefused(Exception):
    pass


class RunInProgress(RunRefused):
    """The job already has a run that has not finished."""

    def __init__(
        self, run: Run, message: str = "This job already has a queued or running run."
    ) -> None:
        super().__init__(message)
        self.run = run


@transaction.atomic
def create_run(
    job: Job,
    scheduled_for: datetime,
    *,
    started_by: RunStart = RunStart.SCHEDULE,
    execution_plan: ExecutionPlan | None = None,
    submitted_by: User | None = None,
) -> Run:
    """Create an assignment and its first attempt in the same transaction."""
    defaults = {
        "started_by": started_by,
        "commit_sha": job.repository.config_sha,
        "input_snapshot": {"history_run_ids": previous_run_ids(job)},
        "execution_plan": (
            build_execution_plan(job) if execution_plan is None else execution_plan
        ).stored(),
    }
    task = create_task(repository=job.repository, submitted_by=submitted_by)
    run = create_run_record(job=job, task=task, scheduled_for=scheduled_for, **defaults)
    record_pending_pull_request(run)
    record_pending_issues(run)
    return run


class RunNotReady(RunRefused):
    """A required approval, connection or model is missing."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems) or "This job is not ready to run.")
        self.problems = problems


def start_run(
    job: Job,
    started_by: RunStart,
    payload: bytes | None = None,
    repeat_key: str | None = None,
    *,
    submitted_by: User | None = None,
) -> Run:
    """Start a job now. Refuses while another run of the job has not finished.

    One run at a time per job also stops a client stuck in a loop from
    queuing runs faster than they finish. A trigger passes the request body as
    payload. The run keeps it with its input. repeat_key lets later requests
    for the same event find this run.
    """
    # Starting now does not need the schedule. It still needs the repository.
    if job.removed_at is not None:
        raise RunRefused(JOB_REMOVED)
    if not job.repository.enabled:
        raise RunRefused("Enable the repository before running its jobs.")
    with transaction.atomic():
        # Two starts at the same moment wait here, so only one creates a run.
        lock_job(job)
        active = latest_active_run(job)
        if active is not None:
            raise RunInProgress(active)
        execution_plan = build_execution_plan(job)
        if not execution_plan.ready:
            raise RunNotReady(execution_problems(job.repository, execution_plan))
        run = create_run(
            job,
            timezone.now(),
            started_by=started_by,
            execution_plan=execution_plan,
            submitted_by=submitted_by,
        )
        if payload is not None:
            run.input_snapshot["payload"] = store_run_payload(run, payload)
        if repeat_key is not None:
            run.input_snapshot["repeat_key"] = repeat_key
        if payload is not None or repeat_key is not None:
            run.save(update_fields=["input_snapshot"])
    enqueue_run(run)
    return run


def create_scheduled_run(job: Job, scheduled_for: datetime) -> Run | None:
    """Create the run for one cron tick, or skip it while another run is active.

    A replayed tick returns the run it already created. A skipped tick is
    noted on the active run.
    """
    with transaction.atomic():
        lock_job(job)
        existing = job_run_for_slot(job, scheduled_for)
        if existing is not None:
            return existing
        active = latest_active_run(job)
        if active is not None:
            add_event(
                active.pk,
                f"Skipped the scheduled run at {scheduled_for:%Y-%m-%d %H:%M %Z} "
                "because this run had not finished.",
            )
            return None
        return create_run(job, scheduled_for)


def start_manual_run(
    job: Job,
    *,
    submitted_by: User | None = None,
    started_by: RunStart = RunStart.MANUAL,
) -> Run:
    """Run now, from the dashboard or an MCP client."""
    return start_run(job, started_by, submitted_by=submitted_by)
