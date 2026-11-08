"""Run listings: search, filters and pages for the runs view."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from django.utils import timezone

from secondshift.runs.dao import run_listing
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


def run_page(
    workspace: Workspace, filters: RunFilters, page: int, page_size: int
) -> RunPage:
    runs, count, page, status_counts = run_listing(
        workspace.pk,
        q=filters.q,
        statuses=filters.statuses,
        job_id=filters.job_id,
        repository_id=filters.repository_id,
        since=period_start(filters.period) if filters.period is not None else None,
        by_cost=filters.sort == "cost",
        page=page,
        page_size=page_size,
    )
    return RunPage(runs, count, page, page_size, status_counts)
