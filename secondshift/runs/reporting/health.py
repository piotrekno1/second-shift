"""Workspace health for the overview: what is failing now, and what it costs."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from secondshift.gateway.dao import spend_per_job, spend_totals
from secondshift.jobs.dao import jobs_by_id
from secondshift.runs.dao import (
    finished_runs_per_day,
    jobs_whose_last_run_failed,
    run_counts,
    runs_by_id,
)
from secondshift.runs.models import Run
from secondshift.runs.reporting.failures import describe_failure
from secondshift.workspaces.models import Workspace

WINDOW = timedelta(days=7)
# Two weeks, so the overview chart shows this week against the one before.
CHART_DAYS = 14


@dataclass(frozen=True)
class FailingJob:
    job_id: int
    job: str
    repository: str
    run_id: int
    failed_at: datetime | None
    label: str
    reason: str
    # Scheduled but switched off, so nothing will run it again on its own.
    paused: bool


@dataclass(frozen=True)
class WeekSummary:
    runs: int
    previous_runs: int
    finished: int
    succeeded: int
    spend_usd: Decimal
    previous_spend_usd: Decimal
    # Some model calls had no known price, so spend is a lower bound.
    spend_partial: bool


@dataclass(frozen=True)
class RunDay:
    """Finished runs on one local day, by outcome."""

    day: date
    succeeded: int
    failed: int
    canceled: int


@dataclass(frozen=True)
class JobSpend:
    job_id: int
    job: str
    repository: str
    runs: int
    spend_usd: Decimal
    # Some of this job's model calls had no known price.
    partial: bool


def failing_jobs(workspace: Workspace) -> list[FailingJob]:
    """Jobs whose most recent finished run failed.

    A job that failed last week but has succeeded since is healthy, so this
    looks only at each job's latest outcome, not at failure history. Paused
    jobs still count, marked as paused, since their last run is still broken.
    """
    jobs = jobs_whose_last_run_failed(workspace.pk)
    runs = runs_by_id(job.latest_run_id for job in jobs)
    failing: list[FailingJob] = []
    for job in jobs:
        run = runs[job.latest_run_id]
        view = describe_failure(run)
        failing.append(
            FailingJob(
                job_id=job.pk,
                job=job.name,
                repository=job.repository.display_name,
                run_id=run.pk,
                failed_at=run.finished_at,
                label=view.label,
                reason=view.reason,
                paused=bool(job.cron) and not job.enabled,
            )
        )
    return failing


def week_summary(workspace: Workspace, now: datetime | None = None) -> WeekSummary:
    """Runs and model spend in the last seven days, with the week before for trend."""
    now = now or timezone.now()
    start, previous_start = now - WINDOW, now - 2 * WINDOW
    runs = run_counts(workspace.pk, start, previous_start)
    totals = spend_totals(
        Q(run__task__repository__workspace_id=workspace.pk), start, previous_start
    )
    return WeekSummary(
        runs=runs["runs"],
        previous_runs=runs["previous"],
        finished=runs["finished"],
        succeeded=runs["succeeded"],
        spend_usd=totals["spend"] or Decimal(0),
        previous_spend_usd=totals["previous"] or Decimal(0),
        spend_partial=totals["unpriced"] > 0,
    )


def runs_by_day(
    workspace: Workspace, now: datetime | None = None, days: int = CHART_DAYS
) -> list[RunDay]:
    """Finished runs per local day, oldest first. Days without runs count as zero."""
    today = timezone.localdate(now or timezone.now())
    first = today - timedelta(days=days - 1)
    rows = finished_runs_per_day(workspace.pk, first, today)
    counts = {(row["day"], row["status"]): row["count"] for row in rows}
    return [
        RunDay(
            day=day,
            succeeded=counts.get((day, Run.Status.SUCCEEDED), 0),
            failed=counts.get((day, Run.Status.FAILED), 0),
            canceled=counts.get((day, Run.Status.CANCELED), 0),
        )
        for day in (first + timedelta(days=offset) for offset in range(days))
    ]


def spend_by_job(
    workspace: Workspace, now: datetime | None = None, limit: int = 5
) -> list[JobSpend]:
    """The jobs that spent the most on models in the last seven days."""
    start = (now or timezone.now()) - WINDOW
    rows = spend_per_job(workspace.pk, start)
    ranked = sorted(rows, key=lambda row: row["spend"] or Decimal(0), reverse=True)
    ranked = ranked[:limit]
    jobs = jobs_by_id(row["run__job_id"] for row in ranked)
    return [
        JobSpend(
            job_id=row["run__job_id"],
            job=jobs[row["run__job_id"]].name,
            repository=jobs[row["run__job_id"]].repository.display_name,
            runs=row["runs"],
            spend_usd=row["spend"] or Decimal(0),
            partial=row["unpriced"] > 0,
        )
        for row in ranked
    ]
