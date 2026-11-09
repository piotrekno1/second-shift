from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from secondshift.config.constants import RunStart
from secondshift.connections.models import AgentConnection
from secondshift.connections.tests.test_agent_connections import (
    agent_login as agent_login_fixture,
)
from secondshift.jobs.models import Job
from secondshift.jobs.planning import build_execution_plan
from secondshift.repositories.models import Repository
from secondshift.runs.dao import create_run_record, create_task
from secondshift.runs.models import Run
from secondshift.runs.orchestration.workflows import (
    RunNotReadyError,
    mark_run_started_step,
)
from secondshift.runs.services import (
    RunNotReady,
    create_scheduled_run,
    start_manual_run,
)
from secondshift.workspaces.models import Workspace, WorkspaceMembership

agent_login = agent_login_fixture
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("secondshift.runs.services.enqueue_run", lambda run: None)


@pytest.fixture
def job(agent_login: tuple[Workspace, AgentConnection, User]) -> Job:
    """A job whose workspace runs on a Claude Code login."""
    workspace, login, _ = agent_login
    workspace.default_agent_connection = login
    workspace.save(update_fields=["default_agent_connection"])
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="example/agents",
        config_sha="a" * 40,
        enabled=True,
    )
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path="SKILL.md",
        config_hash="abc",
    )


def finished_run(job: Job, hours_ago: float = 1, ready: bool = True) -> Run:
    plan = build_execution_plan(job).stored()
    plan["ready"] = ready
    run = create_run_record(
        task=create_task(repository=job.repository),
        started_by=RunStart.SCHEDULE,
        job=job,
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
        status=Run.Status.SUCCEEDED,
        execution_plan=plan,
    )
    created = timezone.now() - timedelta(hours=hours_ago)
    Run.objects.filter(pk=run.pk).update(created_at=created)
    return run


def set_cap(login: AgentConnection, cap: int | None) -> None:
    login.max_runs_per_day = cap
    login.save(update_fields=["max_runs_per_day"])


def test_runs_start_under_the_cap(job: Job) -> None:
    set_cap(job.workspace.default_agent_connection, 2)
    finished_run(job)
    assert start_manual_run(job).execution_plan["ready"] is True


def test_runs_are_refused_at_the_cap(job: Job) -> None:
    set_cap(job.workspace.default_agent_connection, 1)
    finished_run(job)
    with pytest.raises(RunNotReady, match="reached its limit of 1 runs in 24 hours"):
        start_manual_run(job)


def test_no_cap_means_no_limit(job: Job) -> None:
    for _ in range(3):
        finished_run(job)
    assert start_manual_run(job).execution_plan["ready"] is True


def test_runs_older_than_a_day_do_not_count(job: Job) -> None:
    set_cap(job.workspace.default_agent_connection, 1)
    finished_run(job, hours_ago=25)
    assert start_manual_run(job).execution_plan["ready"] is True


def test_refused_runs_do_not_count(job: Job) -> None:
    login = job.workspace.default_agent_connection
    set_cap(login, 1)
    finished_run(job, ready=False)
    assert start_manual_run(job).execution_plan["ready"] is True


def test_runs_with_another_login_do_not_count(
    job: Job, agent_login: tuple[Workspace, AgentConnection, User]
) -> None:
    workspace, _, owner = agent_login
    finished_run(job)
    other = AgentConnection.objects.create(
        workspace=workspace,
        owner=owner,
        backend="claude_code",
        display_name="Other",
        auth_data="token",
        max_runs_per_day=1,
    )
    workspace.default_agent_connection = other
    workspace.save(update_fields=["default_agent_connection"])
    assert start_manual_run(Job.objects.get(pk=job.pk)).execution_plan["ready"]


def test_a_scheduled_run_at_the_cap_fails_with_the_reason(job: Job) -> None:
    set_cap(job.workspace.default_agent_connection, 1)
    finished_run(job)
    run = create_scheduled_run(job, timezone.now())
    assert run is not None
    # The run fails before checkout, without starting a sandbox.
    with pytest.raises(RunNotReadyError, match="reached its limit"):
        mark_run_started_step(run.pk)
    run.refresh_from_db()
    assert run.status == Run.Status.QUEUED


def test_owner_sets_and_clears_the_cap(
    job: Job, agent_login: tuple[Workspace, AgentConnection, User], client: Client
) -> None:
    _, login, owner = agent_login
    finished_run(job)
    client.force_login(owner)
    url = f"/api/agent-connections/{login.pk}/run-limit"

    response = client.post(
        url, {"max_runs_per_day": 5}, content_type="application/json"
    )
    assert response.status_code == 200
    assert response.json()["max_runs_per_day"] == 5
    assert response.json()["recent_runs"] == 1
    listed = client.get("/api/agent-connections").json()
    assert listed[0]["recent_runs"] == 1

    response = client.post(
        url, {"max_runs_per_day": None}, content_type="application/json"
    )
    assert response.json()["max_runs_per_day"] is None
    assert (
        client.post(
            url, {"max_runs_per_day": 0}, content_type="application/json"
        ).status_code
        == 422
    )


def test_only_the_login_owner_sets_its_cap(
    agent_login: tuple[Workspace, AgentConnection, User], client: Client
) -> None:
    workspace, login, _ = agent_login
    other = get_user_model().objects.create_user(username="other-owner")
    WorkspaceMembership.objects.create(
        workspace=workspace, user=other, role=WorkspaceMembership.Role.OWNER
    )
    client.force_login(other)
    response = client.post(
        f"/api/agent-connections/{login.pk}/run-limit",
        {"max_runs_per_day": 5},
        content_type="application/json",
    )
    assert response.status_code == 404
    login.refresh_from_db()
    assert login.max_runs_per_day is None
