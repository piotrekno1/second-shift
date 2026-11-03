"""Create runs from job policy, whether started by cron, by hand or by a trigger."""

from copy import deepcopy
from datetime import datetime
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from secondshift.config.constants import RunStart, TaskOrigin
from secondshift.jobs.models import Job
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.readiness import plan_problems
from secondshift.runs.artifacts import job_input_snapshot, store_run_payload
from secondshift.runs.history import previous_run_ids
from secondshift.runs.models import Run, RunEvent, Task
from secondshift.runs.orchestration.dispatch import enqueue_run


class RunRefused(Exception):
    pass


class RunInProgress(RunRefused):
    """The job already has a run that has not finished."""

    def __init__(self, run: Run) -> None:
        super().__init__("This job already has a queued or running run.")
        self.run = run


@transaction.atomic
def create_run(
    job: Job,
    scheduled_for: datetime,
    *,
    started_by: RunStart = RunStart.SCHEDULE,
    execution_plan: dict[str, Any] | None = None,
    submitted_by: User | None = None,
    origin: TaskOrigin | None = None,
) -> Run:
    """Create an assignment and its first attempt in the same transaction."""
    defaults = {
        "started_by": started_by,
        "commit_sha": job.repository.config_sha,
        "input_snapshot": job_input_snapshot(job)
        | {"history_run_ids": previous_run_ids(job)},
        "execution_plan": (
            build_execution_plan(job) if execution_plan is None else execution_plan
        ),
    }
    task = Task.objects.create(
        job=job,
        origin=origin if origin is not None else TaskOrigin(started_by),
        submitted_by=submitted_by,
        commit_sha=defaults["commit_sha"],
        input_snapshot=deepcopy(defaults["input_snapshot"]),
        scheduled_for=scheduled_for,
    )
    return Run.objects.create(
        job=job, task=task, scheduled_for=scheduled_for, **defaults
    )


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
    origin: TaskOrigin | None = None,
) -> Run:
    """Start a job now. Refuses while another run of the job has not finished.

    One run at a time per job also stops a client stuck in a loop from
    queuing runs faster than they finish. A trigger passes the request body as
    payload. The run keeps it with its input. repeat_key lets later requests
    for the same event find this run.
    """
    # Starting now does not need the schedule. It still needs the repository.
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
            raise RunNotReady(plan_problems(job, execution_plan))
        run = create_run(
            job,
            timezone.now(),
            started_by=started_by,
            execution_plan=execution_plan,
            submitted_by=submitted_by,
            origin=origin,
        )
        if payload is not None:
            run.input_snapshot["payload"] = store_run_payload(run, payload)
        if repeat_key is not None:
            run.input_snapshot["repeat_key"] = repeat_key
        if payload is not None or repeat_key is not None:
            run.save(update_fields=["input_snapshot"])
            Task.objects.filter(pk=run.task_id).update(
                input_snapshot=deepcopy(run.input_snapshot)
            )
    enqueue_run(run)
    return run


def create_scheduled_run(job: Job, scheduled_for: datetime) -> Run | None:
    """Create the run for one cron tick, or skip it while another run is active.

    A replayed tick returns the run it already created. A skipped tick is
    noted on the active run.
    """
    with transaction.atomic():
        Job.objects.select_for_update().filter(pk=job.pk).first()
        existing = job.runs.filter(scheduled_for=scheduled_for).first()
        if existing is not None:
            return existing
        active = job.runs.filter(status__in=Run.ACTIVE_STATUSES).order_by("-pk").first()
        if active is not None:
            RunEvent.objects.create(
                run=active,
                message=(
                    f"Skipped the scheduled run at {scheduled_for:%Y-%m-%d %H:%M %Z} "
                    "because this run had not finished."
                ),
            )
            return None
        return create_run(job, scheduled_for)


def start_manual_run(
    job: Job,
    *,
    submitted_by: User | None = None,
    origin: TaskOrigin = TaskOrigin.MANUAL,
) -> Run:
    """Run now."""
    return start_run(job, RunStart.MANUAL, submitted_by=submitted_by, origin=origin)
