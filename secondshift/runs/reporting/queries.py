"""Run listings: search, filters and pages for the runs view."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone

from secondshift.gateway.usage import with_model_usage
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace

Period = Literal["24h", "7d", "30d"]
RunSort = Literal["newest", "cost"]
PERIODS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def period_start(period: Period, now: datetime | None = None) -> datetime:
    """The same window the overview counts over, so its links match its numbers."""
    return (now or timezone.now()) - PERIODS[period]


@dataclass(frozen=True)
class RunFilters:
    q: str = ""
    statuses: list[str] = field(default_factory=list)
    job_id: int | None = None
    repository_id: int | None = None
    period: Period | None = None
    sort: RunSort = "newest"


@dataclass(frozen=True)
class RunPage:
    runs: list[Run]
    count: int
    page: int
    page_size: int
    status_counts: dict[str, int]


def search_runs(runs: QuerySet[Run], q: str) -> QuerySet[Run]:
    q = q.strip()
    if not q:
        return runs
    match = (
        Q(job__name__icontains=q)
        | Q(job__key__icontains=q)
        | Q(job__repository__full_name__icontains=q)
        | Q(summary__icontains=q)
    )
    # "#42" or "42" also finds run 42.
    if (number := q.removeprefix("#")).isdigit():
        match |= Q(pk=int(number))
    return runs.filter(match)


def filtered_runs(workspace: Workspace, filters: RunFilters) -> QuerySet[Run]:
    """Runs matching every filter except status, which callers apply last."""
    runs = Run.objects.filter(job__workspace=workspace)
    if filters.job_id is not None:
        runs = runs.filter(job_id=filters.job_id)
    if filters.repository_id is not None:
        runs = runs.filter(job__repository_id=filters.repository_id)
    if filters.period is not None:
        runs = runs.filter(created_at__gte=period_start(filters.period))
    return search_runs(runs, filters.q)


def run_page(
    workspace: Workspace, filters: RunFilters, page: int, page_size: int
) -> RunPage:
    runs = filtered_runs(workspace, filters)
    # Counts ignore the status filter, so each status chip shows what it would add.
    status_counts = {
        row["status"]: row["total"]
        for row in runs.order_by().values("status").annotate(total=Count("pk"))
    }
    if filters.statuses:
        runs = runs.filter(status__in=filters.statuses)
    count = runs.count()
    last_page = max((count + page_size - 1) // page_size, 1)
    page = min(page, last_page)
    start = (page - 1) * page_size
    ordered = with_model_usage(runs.select_related("job__repository"))
    if filters.sort == "cost":
        ordered = ordered.order_by(
            F("model_cost_usd").desc(nulls_last=True), "-created_at", "-pk"
        )
    else:
        ordered = ordered.order_by("-created_at", "-pk")
    items = list(ordered[start : start + page_size])
    return RunPage(items, count, page, page_size, status_counts)
