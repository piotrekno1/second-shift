from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from pydantic import ValidationError

from secondshift.connections.models import Connection
from secondshift.gateway.models import RunToken
from secondshift.gateway.tokens import issue_run_token
from secondshift.jobs.limits import (
    LIMIT_SPECS,
    LimitError,
    RunLimit,
    effective_limits,
    validate_overrides,
)
from secondshift.jobs.manifest import RepositoryManifest
from secondshift.jobs.models import Job
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.tests.test_planning import create_job
from secondshift.runs.execution.budgets import Budget, budget_summary
from secondshift.runs.models import Run
from secondshift.workspaces.models import WorkspaceMembership
from secondshift.workspaces.services import workspace_owner


@pytest.fixture
def ceilings(settings: Any) -> Any:
    settings.AGENT_MAX_TURNS = 24
    settings.AGENT_MAX_TURNS_CEILING = 100
    settings.AGENT_MAX_TOOL_CALLS = 64
    settings.AGENT_MAX_TOOL_CALLS_CEILING = 64
    settings.MODEL_RUN_TOKEN_BUDGET = 2_000_000
    settings.MODEL_RUN_TOKEN_BUDGET_CEILING = 10_000_000
    settings.RUN_TIMEOUT_SECONDS = 900
    settings.RUN_TIMEOUT_SECONDS_CEILING = 3600
    return settings


@pytest.fixture
def job(db: object, ceilings: Any) -> Job:
    job = create_job(db)
    job.repository.enabled = True
    job.repository.save(update_fields=["enabled"])
    return job


def post(client: Client, path: str, body: dict[str, Any]) -> Any:
    return client.post(path, body, content_type="application/json")


@pytest.mark.django_db
def test_a_job_without_choices_gets_the_server_defaults(job: Job) -> None:
    assert effective_limits(job) == {
        "max_turns": 24,
        "max_tool_calls": 64,
        "token_budget": 2_000_000,
        "timeout_seconds": 900,
    }


@pytest.mark.django_db
def test_a_job_choice_wins_over_the_workspace(job: Job) -> None:
    job.workspace.max_turns = 40
    job.workspace.token_budget = 5_000_000
    job.workspace.save()
    job.max_turns = 60
    job.save()

    limits = effective_limits(job)

    assert limits["max_turns"] == 60
    assert limits["token_budget"] == 5_000_000
    assert limits["max_tool_calls"] == 64


@pytest.mark.django_db
def test_a_lowered_maximum_caps_earlier_choices(job: Job, ceilings: Any) -> None:
    job.max_turns = 90
    job.save()
    ceilings.AGENT_MAX_TURNS_CEILING = 50

    assert effective_limits(job)["max_turns"] == 50


@pytest.mark.django_db
def test_the_plan_freezes_every_limit(job: Job) -> None:
    job.token_budget = 3_000_000
    job.timeout_seconds = 1800
    job.save()

    limits = build_execution_plan(job)["agent"]["limits"]

    assert limits["token_budget"] == 3_000_000
    assert limits["timeout_seconds"] == 1800
    assert limits["max_turns"] == 24


def test_owner_input_must_stay_within_the_server_bounds(ceilings: Any) -> None:
    assert validate_overrides({"max_turns": 100, "token_budget": None}) == {
        "max_turns": 100,
        "token_budget": None,
    }
    with pytest.raises(LimitError, match="between 1 and 100") as refused:
        validate_overrides({"max_turns": 101})
    assert refused.value.ceiling_env == "SECONDSHIFT_AGENT_MAX_TURNS_CEILING"
    with pytest.raises(LimitError, match="between 60"):
        validate_overrides({"timeout_seconds": 30})
    with pytest.raises(LimitError, match="Unknown limit"):
        validate_overrides({"max_tool_result_chars": 10})


def test_repository_config_cannot_set_limits() -> None:
    job = {"name": "Review", "skill": ".agents/skills/review/SKILL.md"}
    for extra in (
        {"limits": {"max_turns": 500}},
        {"max_turns": 500},
        {"token_budget": 10**9},
        {"timeout_seconds": 86_400},
    ):
        with pytest.raises(ValidationError, match="Extra inputs"):
            RepositoryManifest.model_validate({"jobs": {"review": {**job, **extra}}})


@pytest.mark.django_db
def test_the_owner_changes_workspace_limits(client: Client, job: Job) -> None:
    client.force_login(workspace_owner(job.workspace))

    response = post(client, "/api/settings/limits", {"max_turns": 50})

    assert response.status_code == 200
    body = response.json()
    assert body["can_edit_limits"] is True
    turns = next(item for item in body["limits"] if item["key"] == "max_turns")
    assert turns["value"] == 50
    assert turns["override"] == 50
    assert turns["inherited"] == 24
    assert turns["maximum"] == 100
    job.workspace.refresh_from_db()
    assert job.workspace.max_turns == 50

    # A field left out stays. Null goes back to the server default.
    post(client, "/api/settings/limits", {"token_budget": 4_000_000})
    post(client, "/api/settings/limits", {"max_turns": None})
    job.workspace.refresh_from_db()
    assert job.workspace.max_turns is None
    assert job.workspace.token_budget == 4_000_000


@pytest.mark.django_db
def test_out_of_range_limits_are_refused(client: Client, job: Job) -> None:
    client.force_login(workspace_owner(job.workspace))

    too_many = post(client, "/api/settings/limits", {"max_tool_calls": 65})
    negative = post(client, f"/api/jobs/{job.pk}/limits", {"max_turns": -1})

    assert too_many.status_code == 422
    # The owner is not staff, so the message does not name a server variable.
    assert too_many.json()["detail"] == "Tool calls must be between 0 and 64."
    assert negative.status_code == 422
    job.refresh_from_db()
    job.workspace.refresh_from_db()
    assert job.workspace.max_tool_calls is None
    assert job.max_turns is None


@pytest.mark.django_db
def test_only_the_owner_changes_limits(client: Client, job: Job) -> None:
    member = get_user_model().objects.create_user(username="member")
    WorkspaceMembership.objects.create(workspace=job.workspace, user=member)
    client.force_login(member)

    workspace = post(client, "/api/settings/limits", {"max_turns": 50})
    for_job = post(client, f"/api/jobs/{job.pk}/limits", {"max_turns": 50})

    assert workspace.status_code == 403
    assert for_job.status_code == 403
    assert client.get("/api/settings").json()["can_edit_limits"] is False
    assert client.get(f"/api/jobs/{job.pk}").json()["can_edit_limits"] is False


@pytest.mark.django_db
def test_job_limits_inherit_from_the_workspace(client: Client, job: Job) -> None:
    job.workspace.timeout_seconds = 1200
    job.workspace.save()
    client.force_login(workspace_owner(job.workspace))

    response = post(client, f"/api/jobs/{job.pk}/limits", {"max_turns": 80})

    assert response.status_code == 200
    limits = {item["key"]: item for item in response.json()["limits"]}
    assert limits["max_turns"]["value"] == 80
    assert limits["timeout_seconds"]["override"] is None
    assert limits["timeout_seconds"]["inherited"] == 1200
    assert limits["timeout_seconds"]["value"] == 1200


@pytest.mark.django_db
def test_a_created_run_keeps_its_limits(
    client: Client, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("secondshift.runs.services.enqueue_run", lambda run: None)
    client.force_login(workspace_owner(job.workspace))
    first = client.post(f"/api/jobs/{job.pk}/run").json()
    Run.objects.filter(pk=first["id"]).update(status=Run.Status.SUCCEEDED)

    post(client, f"/api/jobs/{job.pk}/limits", {"token_budget": 6_000_000})
    second = client.post(f"/api/jobs/{job.pk}/run").json()

    assert first["token_budget"] == 2_000_000
    assert second["token_budget"] == 6_000_000
    run = Run.objects.get(pk=first["id"])
    assert run.execution_plan["agent"]["limits"]["token_budget"] == 2_000_000


@pytest.mark.django_db
def test_the_gateway_token_uses_the_frozen_budget(job: Job) -> None:
    job.token_budget = 7_000_000
    job.save()
    run = Run.objects.create(
        job=job,
        commit_sha="abc",
        scheduled_for=timezone.now(),
        started_at=timezone.now(),
        execution_plan=build_execution_plan(job),
    )
    job.token_budget = None
    job.save()
    connection = Connection.objects.get(workspace=job.workspace, kind="model")

    issue_run_token(run, connection)

    assert RunToken.objects.get(run=run).budget_tokens == 7_000_000


@pytest.mark.django_db
def test_a_run_stopped_by_a_limit_says_so(client: Client, job: Job) -> None:
    run = Run.objects.create(
        job=job,
        commit_sha="abc",
        scheduled_for=timezone.now(),
        status=Run.Status.FAILED,
        summary=budget_summary(Budget.TOKENS, 2_000_000),
        execution_plan=build_execution_plan(job),
    )
    client.force_login(workspace_owner(job.workspace))

    body = client.get(f"/api/runs/{run.pk}").json()

    assert body["failure"]["label"] == "Budget used up"
    assert body["failure"]["limit"] is True
    assert body["failure"]["reason"].endswith("Raise it in the job's limits.")
    assert body["token_budget"] == 2_000_000


def test_a_limit_at_the_maximum_points_to_the_server(ceilings: Any) -> None:
    summary = budget_summary(Budget.TOOL_CALLS, 64)

    assert summary.endswith(
        "That is the server maximum. Whoever runs this server can raise it."
    )
    assert "SECONDSHIFT" not in summary


def test_every_limit_has_a_maximum_setting(ceilings: Any) -> None:
    for limit in RunLimit:
        assert hasattr(ceilings, f"{LIMIT_SPECS[limit].setting}_CEILING")
