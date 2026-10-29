"""Data builders and the query budget check shared by the benchmarks."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db import connection
from django.test.utils import CaptureQueriesContext

from secondshift.gateway.models import ModelUsage
from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace
from secondshift.workspaces.services import create_workspace, workspace_owner

# Seeded sizes. Large enough that a missing index or an N+1 query shows up.
JOBS = 20
RUNS_PER_JOB = 250
USAGE_PER_RUN = 3
DAYS = 30
STATUSES = [
    Run.Status.SUCCEEDED,
    Run.Status.SUCCEEDED,
    Run.Status.SUCCEEDED,
    Run.Status.FAILED,
    Run.Status.CANCELED,
]


@contextmanager
def query_budget(budget: int) -> Iterator[CaptureQueriesContext]:
    """Fail when the block runs more queries than `budget`.

    Query counts do not depend on the machine, so they catch N+1 regressions
    in CI where timings are too noisy to compare.
    """
    with CaptureQueriesContext(connection) as captured:
        yield captured
    count = len(captured.captured_queries)
    assert count <= budget, f"{count} queries, budget is {budget}"


def make_workspace(slug: str = "bench") -> Workspace:
    user = get_user_model().objects.create_user(username=f"{slug}-owner")
    return create_workspace(name=slug, slug=slug, owner=user)


def owner_of(workspace: Workspace) -> AbstractUser:
    return workspace_owner(workspace)


def seed_runs(workspace: Workspace) -> list[Job]:
    """Jobs across a few repositories, with runs and model usage over 30 days."""
    repositories = [
        Repository.objects.create(workspace=workspace, full_name=f"acme/repo-{index}")
        for index in range(4)
    ]
    jobs = Job.objects.bulk_create(
        Job(
            workspace=workspace,
            repository=repositories[index % len(repositories)],
            key=f"job-{index}",
            name=f"Job {index}",
            skill_path=f".agents/skills/job-{index}/SKILL.md",
            cron="0 9 * * *",
            enabled=True,
            config_hash="hash",
        )
        for index in range(JOBS)
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    runs = Run.objects.bulk_create(
        Run(
            job=job,
            status=STATUSES[index % len(STATUSES)],
            commit_sha="a" * 40,
            summary=f"Run {index} of {job.name}: checked 12 pull requests.",
            scheduled_for=start + timedelta(hours=index),
        )
        for job in jobs
        for index in range(RUNS_PER_JOB)
    )
    spread_over_days(runs)
    ModelUsage.objects.bulk_create(
        ModelUsage(
            run=run,
            provider="anthropic",
            model="claude-sonnet-5-5",
            status_code=200,
            input_tokens=12_000,
            output_tokens=900,
            cache_read_tokens=40_000,
            cost_usd=Decimal("0.0421") if index else None,
        )
        for run in runs
        for index in range(USAGE_PER_RUN)
    )
    return jobs


def spread_over_days(runs: list[Run]) -> None:
    """bulk_create stamps every run with now, so move them back across days."""
    now = datetime.now(UTC)
    for day in range(DAYS):
        ids = [run.pk for run in runs[day::DAYS]]
        Run.objects.filter(pk__in=ids).update(created_at=now - timedelta(days=day))
