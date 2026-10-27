"""Workspace health for the overview: what is failing now, and what it costs."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from secondshift.gateway.models import ModelUsage
from secondshift.jobs.models import Job
from secondshift.runs.models import Run
from secondshift.runs.reporting.failures import describe_failure
from secondshift.workspaces.models import Workspace

FINISHED = (Run.Status.SUCCEEDED, Run.Status.FAILED, Run.Status.CANCELED)
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
    """Enabled and manual-only jobs whose most recent finished run failed.

    A job that failed last week but has succeeded since is healthy, so this
    looks only at each job's latest outcome, not at failure history. Paused
    scheduled jobs are left out: nothing will run them until someone acts.
    Manual-only jobs have no schedule to pause, so they always count.
    """
    latest = Run.objects.filter(job=OuterRef("pk"), status__in=FINISHED).order_by(
        "-created_at", "-pk"
    )
    jobs = (
        Job.objects.filter(
            Q(enabled=True) | Q(cron=""),
            workspace=workspace,
            repository__removed_at__isnull=True,
        )
        .select_related("repository")
        .annotate(
            latest_run_id=Subquery(latest.values("pk")[:1]),
            latest_status=Subquery(latest.values("status")[:1]),
        )
        .filter(latest_status=Run.Status.FAILED)
        .order_by("name")
    )
    runs = Run.objects.in_bulk([job.latest_run_id for job in jobs])
    failing: list[FailingJob] = []
    for job in jobs:
        run = runs[job.latest_run_id]
        view = describe_failure(run.summary)
        failing.append(
            FailingJob(
                job_id=job.pk,
                job=job.name,
                repository=job.repository.display_name,
                run_id=run.pk,
                failed_at=run.finished_at,
                label=view.label,
                reason=view.reason,
            )
        )
    return failing


def week_summary(workspace: Workspace, now: datetime | None = None) -> WeekSummary:
    """Runs and model spend in the last seven days, with the week before for trend."""
    now = now or timezone.now()
    start, previous_start = now - WINDOW, now - 2 * WINDOW
    runs = Run.objects.filter(job__workspace=workspace, created_at__gte=start)
    usage = ModelUsage.objects.filter(run__job__workspace=workspace)
    totals = usage.aggregate(
        spend=Sum("cost_usd", filter=Q(created_at__gte=start)),
        previous=Sum(
            "cost_usd", filter=Q(created_at__gte=previous_start, created_at__lt=start)
        ),
    )
    return WeekSummary(
        runs=runs.count(),
        previous_runs=Run.objects.filter(
            job__workspace=workspace,
            created_at__gte=previous_start,
            created_at__lt=start,
        ).count(),
        finished=runs.filter(status__in=FINISHED).count(),
        succeeded=runs.filter(status=Run.Status.SUCCEEDED).count(),
        spend_usd=totals["spend"] or Decimal(0),
        previous_spend_usd=totals["previous"] or Decimal(0),
        spend_partial=usage.filter(
            created_at__gte=start, cost_usd__isnull=True
        ).exists(),
    )


def runs_by_day(
    workspace: Workspace, now: datetime | None = None, days: int = CHART_DAYS
) -> list[RunDay]:
    """Finished runs per local day, oldest first. Days without runs count as zero."""
    today = timezone.localdate(now or timezone.now())
    first = today - timedelta(days=days - 1)
    rows = (
        Run.objects.filter(
            job__workspace=workspace,
            status__in=FINISHED,
            created_at__date__gte=first,
            created_at__date__lte=today,
        )
        .annotate(day=TruncDate("created_at"))
        .values("day", "status")
        .annotate(count=Count("pk"))
        .order_by()
    )
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
    rows = (
        ModelUsage.objects.filter(run__job__workspace=workspace, created_at__gte=start)
        .values("run__job_id")
        .annotate(
            spend=Sum("cost_usd"),
            runs=Count("run_id", distinct=True),
            unpriced=Count("pk", filter=Q(cost_usd__isnull=True)),
        )
        .order_by()
    )
    ranked = sorted(rows, key=lambda row: row["spend"] or Decimal(0), reverse=True)
    ranked = ranked[:limit]
    jobs = Job.objects.select_related("repository").in_bulk(
        [row["run__job_id"] for row in ranked]
    )
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
