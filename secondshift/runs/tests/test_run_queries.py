from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from secondshift.gateway.models import ModelUsage
from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace
from secondshift.workspaces.services import create_workspace, workspace_owner


def make_workspace(slug: str) -> Workspace:
    user = get_user_model().objects.create_user(username=f"{slug}-owner")
    return create_workspace(name=slug, slug=slug, owner=user)


def make_job(workspace: Workspace, repository: str, key: str) -> Job:
    repo, _ = Repository.objects.get_or_create(
        workspace=workspace, full_name=repository
    )
    return Job.objects.create(
        workspace=workspace,
        repository=repo,
        key=key,
        name=key.replace("-", " ").title(),
        skill_path=f".agents/skills/{key}/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )


def make_run(job: Job, status: str, summary: str = "", day: int = 1) -> Run:
    return Run.objects.create(
        job=job,
        status=status,
        commit_sha="abc",
        summary=summary,
        scheduled_for=datetime(2026, 10, day, tzinfo=UTC),
    )


@pytest.fixture
def runs(db: object) -> dict[str, object]:
    workspace = make_workspace("team")
    health = make_job(workspace, "acme/api", "repository-health")
    triage = make_job(workspace, "acme/web", "issue-triage")
    created = {
        "health_failed": make_run(health, Run.Status.FAILED, "Timed out", day=1),
        "health_ok": make_run(health, Run.Status.SUCCEEDED, "All green", day=2),
        "triage_ok": make_run(triage, Run.Status.SUCCEEDED, "Labelled 3 issues", day=3),
        "triage_running": make_run(triage, Run.Status.RUNNING, day=4),
    }
    other = make_workspace("other")
    make_run(make_job(other, "acme/api", "repository-health"), Run.Status.FAILED)
    return {"workspace": workspace, "health": health, "triage": triage, **created}


def ids(response: object) -> set[int]:
    return {item["id"] for item in response.json()["items"]}  # type: ignore[attr-defined]


@pytest.mark.django_db
def test_runs_are_scoped_filtered_and_counted(
    client: Client, runs: dict[str, object]
) -> None:
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]

    everything = client.get("/api/runs")
    failed = client.get("/api/runs", {"status": ["failed", "running"]})
    by_job = client.get("/api/runs", {"job_id": runs["health"].pk})  # type: ignore[attr-defined]
    by_repository = client.get(
        "/api/runs",
        {"repository_id": runs["triage"].repository_id},  # type: ignore[attr-defined]
    )

    assert everything.json()["count"] == 4
    assert failed.json()["count"] == 2
    assert ids(failed) == {runs["health_failed"].pk, runs["triage_running"].pk}  # type: ignore[attr-defined]
    # Status counts ignore the status filter but respect the others.
    assert failed.json()["status_counts"] == {"failed": 1, "succeeded": 2, "running": 1}
    assert ids(by_job) == {runs["health_failed"].pk, runs["health_ok"].pk}  # type: ignore[attr-defined]
    assert by_job.json()["status_counts"] == {"failed": 1, "succeeded": 1}
    assert ids(by_repository) == {runs["triage_ok"].pk, runs["triage_running"].pk}  # type: ignore[attr-defined]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("triage", {"triage_ok", "triage_running"}),
        ("acme/api", {"health_failed", "health_ok"}),
        ("labelled", {"triage_ok"}),
        ("TIMED", {"health_failed"}),
    ],
)
def test_runs_search_job_repository_and_summary(
    client: Client, runs: dict[str, object], q: str, expected: set[str]
) -> None:
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]
    response = client.get("/api/runs", {"q": q})
    assert ids(response) == {runs[name].pk for name in expected}  # type: ignore[attr-defined]


@pytest.mark.django_db
def test_runs_search_by_number(client: Client, runs: dict[str, object]) -> None:
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]
    target = runs["health_ok"].pk  # type: ignore[attr-defined]
    assert target in ids(client.get("/api/runs", {"q": f"#{target}"}))


@pytest.mark.django_db
def test_runs_paginate_newest_first_and_clamp_the_page(
    client: Client, runs: dict[str, object]
) -> None:
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]

    first = client.get("/api/runs", {"page_size": 3}).json()
    second = client.get("/api/runs", {"page_size": 3, "page": 2}).json()
    beyond = client.get("/api/runs", {"page_size": 3, "page": 9}).json()

    assert [len(first["items"]), len(second["items"])] == [3, 1]
    assert first["items"][0]["id"] > first["items"][-1]["id"]
    assert beyond["page"] == 2 and beyond["items"] == second["items"]


@pytest.mark.django_db
def test_runs_reject_invalid_queries(client: Client, runs: dict[str, object]) -> None:
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]
    assert client.get("/api/runs", {"status": "exploded"}).status_code == 422
    assert client.get("/api/runs", {"page_size": 500}).status_code == 422
    assert client.get("/api/runs", {"page": 0}).status_code == 422


@pytest.mark.django_db
def test_run_detail_includes_events_output_and_frozen_inputs(
    client: Client, runs: dict[str, object]
) -> None:
    run = runs["health_failed"]
    run.input_snapshot = {"job_key": "frozen-key", "skill_path": "frozen/SKILL.md"}  # type: ignore[attr-defined]
    run.execution_plan = {  # type: ignore[attr-defined]
        "model": {"connection_name": "OpenRouter", "default_model": "m-1"}
    }
    run.output_excerpt = "report tail"  # type: ignore[attr-defined]
    run.save()  # type: ignore[attr-defined]
    run.events.create(message="Run started.")  # type: ignore[attr-defined]
    run.events.create(level="error", message="Timed out", metadata={"stage": "skill"})  # type: ignore[attr-defined]
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]

    body = client.get(f"/api/runs/{run.pk}").json()  # type: ignore[attr-defined]

    assert body["id"] == run.pk  # type: ignore[attr-defined]
    assert (body["has_input"], body["has_output"]) == (False, False)
    assert body["status"] == "failed"
    assert (body["job_key"], body["skill_path"]) == ("frozen-key", "frozen/SKILL.md")
    assert (body["model_provider"], body["model"]) == ("OpenRouter", "m-1")
    assert body["output_excerpt"] == "report tail"
    assert [(e["level"], e["message"]) for e in body["events"]] == [
        ("info", "Run started."),
        ("error", "Timed out"),
    ]
    assert "metadata" not in body["events"][0]


@pytest.mark.django_db
def test_run_detail_is_scoped_to_the_workspace(
    client: Client, runs: dict[str, object]
) -> None:
    foreign = Run.objects.exclude(job__workspace=runs["workspace"]).get()
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]

    assert client.get(f"/api/runs/{foreign.pk}").status_code == 404
    assert client.get("/api/runs/999999").status_code == 404


@pytest.mark.django_db
def test_run_list_includes_stats_from_the_frozen_plan(
    client: Client, runs: dict[str, object]
) -> None:
    run = runs["health_ok"]
    run.started_at = datetime(2026, 10, 2, 9, 0, 0, tzinfo=UTC)  # type: ignore[attr-defined]
    run.execution_plan = {  # type: ignore[attr-defined]
        "agent": {"limits": {"max_turns": 12}},
        "model": {"default_model": "m-1"},
    }
    run.save()  # type: ignore[attr-defined]
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]

    items = client.get("/api/runs").json()["items"]
    item = next(i for i in items if i["id"] == run.pk)  # type: ignore[attr-defined]
    bare = next(i for i in items if i["id"] == runs["health_failed"].pk)  # type: ignore[attr-defined]

    assert item["started_at"].startswith("2026-10-02T09:00:00")
    assert (item["model"], item["max_turns"], item["commit_sha"]) == ("m-1", 12, "abc")
    assert (item["model_requests"], item["model_input_tokens"]) == (0, 0)
    assert (bare["model"], bare["max_turns"]) == (None, None)


@pytest.mark.django_db
def test_run_list_splits_failures_into_label_and_reason(
    client: Client, runs: dict[str, object]
) -> None:
    Run.objects.filter(pk=runs["health_failed"].pk).update(  # type: ignore[attr-defined]
        summary="Couldn't start the sandbox: Runner answered HTTP 500."
    )
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]

    items = {i["id"]: i for i in client.get("/api/runs").json()["items"]}

    assert items[runs["health_failed"].pk]["failure"] == {  # type: ignore[attr-defined]
        "label": "Sandbox",
        "reason": "Runner answered HTTP 500.",
        "limit": False,
        "connection_id": None,
        "next_step": "",
    }
    assert items[runs["health_ok"].pk]["failure"] is None  # type: ignore[attr-defined]


@pytest.mark.django_db
def test_job_detail_lists_recent_runs_newest_first(
    client: Client, runs: dict[str, object]
) -> None:
    health = runs["health"]
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]

    recent = client.get(f"/api/jobs/{health.pk}").json()["recent_runs"]  # type: ignore[attr-defined]

    assert [run["id"] for run in recent] == [
        runs["health_ok"].pk,  # type: ignore[attr-defined]
        runs["health_failed"].pk,  # type: ignore[attr-defined]
    ]


def record_usage(run: Run, cost: str | None) -> None:
    ModelUsage.objects.create(
        run=run,
        provider="openai_compatible",
        model="m",
        status_code=200,
        cost_usd=None if cost is None else Decimal(cost),
    )


@pytest.mark.django_db
def test_runs_filter_by_period_and_sort_by_cost(
    client: Client, runs: dict[str, object]
) -> None:
    old = runs["health_failed"]
    Run.objects.filter(pk=old.pk).update(  # type: ignore[attr-defined]
        created_at=timezone.now() - timedelta(days=10)
    )
    record_usage(runs["health_ok"], "0.0200")  # type: ignore[arg-type]
    record_usage(runs["triage_ok"], "0.0500")  # type: ignore[arg-type]
    record_usage(runs["triage_running"], None)  # type: ignore[arg-type]
    client.force_login(workspace_owner(runs["workspace"]))  # type: ignore[attr-defined]

    week = client.get("/api/runs", {"period": "7d"})
    month = client.get("/api/runs", {"period": "30d"})
    by_cost = client.get("/api/runs", {"sort": "cost"}).json()["items"]

    assert old.pk not in ids(week) and old.pk in ids(month)  # type: ignore[attr-defined]
    assert week.json()["status_counts"].get("failed") is None
    # Priced runs first, most expensive first; unpriced runs after them.
    assert [item["id"] for item in by_cost[:3]] == [
        runs["triage_ok"].pk,  # type: ignore[attr-defined]
        runs["health_ok"].pk,  # type: ignore[attr-defined]
        runs["triage_running"].pk,  # type: ignore[attr-defined]
    ]
    assert client.get("/api/runs", {"period": "1y"}).status_code == 422
    assert client.get("/api/runs", {"sort": "slowest"}).status_code == 422
