from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.test import Client

from secondshift.automations.health import failing_jobs, spend_by_job, week_summary
from secondshift.automations.models import Run
from secondshift.automations.tests.test_run_queries import make_job, make_workspace
from secondshift.gateway.models import ModelUsage

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def add_run(job, status: str, minutes_ago: int, summary: str = "") -> Run:
    run = Run.objects.create(
        job=job,
        status=status,
        commit_sha="abc",
        summary=summary,
        scheduled_for=NOW - timedelta(minutes=minutes_ago),
        finished_at=NOW - timedelta(minutes=minutes_ago),
    )
    # created_at is set on insert; move it to the run's own time.
    Run.objects.filter(pk=run.pk).update(
        created_at=NOW - timedelta(minutes=minutes_ago)
    )
    return Run.objects.get(pk=run.pk)


def add_usage(run: Run, cost: str | None, days_ago: float) -> None:
    usage = ModelUsage.objects.create(
        run=run,
        provider="openai_compatible",
        model="m",
        status_code=200,
        cost_usd=None if cost is None else Decimal(cost),
    )
    ModelUsage.objects.filter(pk=usage.pk).update(
        created_at=NOW - timedelta(days=days_ago)
    )


@pytest.mark.django_db
def test_failing_jobs_look_only_at_each_jobs_latest_finished_run() -> None:
    workspace = make_workspace("team")
    recovered = make_job(workspace, "acme/api", "recovered")
    broken = make_job(workspace, "acme/api", "broken")
    paused = make_job(workspace, "acme/web", "paused")
    for job in (recovered, broken, paused):
        job.enabled = True
        job.save(update_fields=["enabled"])
    paused.enabled = False
    paused.save(update_fields=["enabled"])

    add_run(recovered, Run.Status.FAILED, 60, "Couldn't start the sandbox: boom.")
    add_run(recovered, Run.Status.SUCCEEDED, 30)
    add_run(broken, Run.Status.SUCCEEDED, 60)
    failed = add_run(
        broken,
        Run.Status.FAILED,
        30,
        "The skill run failed: The model provider answered HTTP 401: Nope.",
    )
    add_run(broken, Run.Status.RUNNING, 1)
    add_run(paused, Run.Status.FAILED, 5, "Timed out")

    [only] = failing_jobs(workspace)

    assert (only.job_id, only.run_id) == (broken.pk, failed.pk)
    assert (only.label, only.reason) == ("Model provider", "HTTP 401 · Nope.")


@pytest.mark.django_db
def test_week_summary_counts_runs_and_compares_spend_with_the_week_before() -> None:
    workspace = make_workspace("team")
    job = make_job(workspace, "acme/api", "review")
    ok = add_run(job, Run.Status.SUCCEEDED, 60)
    add_run(job, Run.Status.FAILED, 120)
    add_run(job, Run.Status.RUNNING, 1)
    old = add_run(job, Run.Status.SUCCEEDED, 60 * 24 * 10)
    add_usage(ok, "0.0100", days_ago=0.1)
    add_usage(ok, None, days_ago=0.1)
    add_usage(old, "0.0400", days_ago=10)
    add_usage(old, "1.0000", days_ago=30)

    week = week_summary(workspace, now=NOW)

    assert (week.runs, week.finished, week.succeeded) == (3, 2, 1)
    assert week.spend_usd == Decimal("0.0100")
    assert week.previous_spend_usd == Decimal("0.0400")
    assert week.spend_partial is True


@pytest.mark.django_db
def test_overview_reports_failing_jobs_and_weekly_numbers(client: Client) -> None:
    workspace = make_workspace("team")
    job = make_job(workspace, "acme/api", "review")
    job.enabled = True
    job.save(update_fields=["enabled"])
    run = Run.objects.create(
        job=job,
        status=Run.Status.FAILED,
        commit_sha="abc",
        summary="Couldn't start the sandbox: Runner answered HTTP 500.",
        scheduled_for=datetime.now(UTC),
    )
    client.force_login(workspace.owner)

    body = client.get("/api/overview").json()

    assert body["failing_jobs"][0]["run_id"] == run.pk
    assert body["failing_jobs"][0]["label"] == "Sandbox"
    assert (body["runs_7d"], body["finished_7d"], body["succeeded_7d"]) == (1, 1, 0)
    assert body["spend_7d_usd"] == 0


@pytest.mark.django_db
def test_spend_by_job_ranks_this_weeks_spend() -> None:
    workspace = make_workspace("team")
    cheap = make_job(workspace, "acme/api", "cheap")
    costly = make_job(workspace, "acme/web", "costly")
    quiet = make_job(workspace, "acme/web", "quiet")
    first, second = (
        add_run(costly, Run.Status.SUCCEEDED, 60),
        add_run(costly, Run.Status.FAILED, 30),
    )
    add_usage(first, "0.0300", days_ago=0.1)
    add_usage(second, "0.0200", days_ago=0.1)
    add_usage(second, None, days_ago=0.1)
    add_usage(add_run(cheap, Run.Status.SUCCEEDED, 60), "0.0010", days_ago=0.1)
    add_usage(add_run(quiet, Run.Status.SUCCEEDED, 60), "9.0000", days_ago=9)

    rows = spend_by_job(workspace, now=NOW)

    assert [(row.job_id, row.runs, row.spend_usd, row.partial) for row in rows] == [
        (costly.pk, 2, Decimal("0.0500"), True),
        (cheap.pk, 1, Decimal("0.0010"), False),
    ]
