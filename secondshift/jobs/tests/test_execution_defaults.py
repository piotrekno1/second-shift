import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from secondshift.config.api import api
from secondshift.config.constants import AgentBackend
from secondshift.connections.models import AgentConnection, Connection
from secondshift.connections.tests.test_agent_connections import (
    TOKEN,
)
from secondshift.connections.tests.test_agent_connections import (
    agent_login as agent_login_fixture,
)
from secondshift.jobs.execution import (
    MODEL_CHOICE,
    execution_configuration,
    job_execution,
    save_execution_preference,
)
from secondshift.jobs.models import Job
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.serialization import serialize_workspace_jobs
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace, WorkspaceMembership

agent_login = agent_login_fixture
CODEX_AUTH = {
    "tokens": {
        key: f"test-{key}"
        for key in ("access_token", "refresh_token", "id_token", "account_id")
    }
}
pytestmark = pytest.mark.django_db


@pytest.fixture
def agent_run(
    agent_login: tuple[Workspace, AgentConnection, User],
) -> tuple[Run, User]:
    """A running job whose workspace default is a Claude Code login."""
    workspace, login, owner = agent_login
    workspace.default_agent_connection = login
    workspace.save(update_fields=["default_agent_connection"])
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/agents", config_sha="a" * 40
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path="SKILL.md",
        config_hash="abc",
    )
    run = Run.objects.create(
        job=job,
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
        started_at=timezone.now(),
        status=Run.Status.RUNNING,
        execution_plan=build_execution_plan(job),
    )
    return run, owner


def model_workspace(run: Run) -> Job:
    job = run.job
    job.workspace.default_agent_connection = None
    job.workspace.save(update_fields=["default_agent_connection"])
    return job


def add_model(run: Run) -> Connection:
    return Connection.objects.create(
        workspace=run.job.workspace,
        kind=Connection.Kind.MODEL,
        provider=Connection.Provider.OPENAI,
        display_name="Workspace API",
        default_model="test-model",
        is_default=True,
    )


def connect_login(client: Client, owner: User) -> AgentConnection:
    client.force_login(owner)
    response = client.post(
        "/api/agent-connections",
        data={"backend": "codex", "auth": json.dumps(CODEX_AUTH)},
        content_type="application/json",
    )
    assert response.status_code == 200
    return AgentConnection.objects.get(pk=response.json()["id"])


def test_a_first_login_runs_jobs_when_no_model_provider_works(
    agent_run: tuple[Run, User], client: Client
) -> None:
    run, owner = agent_run
    job = model_workspace(run)
    login = connect_login(client, owner)
    job.workspace.refresh_from_db()
    assert job.workspace.default_agent_connection == login
    assert execution_configuration(job.workspace, owner)["selected"] == (
        f"agent:{login.pk}"
    )


def test_a_new_login_keeps_a_working_model_provider(
    agent_run: tuple[Run, User], client: Client
) -> None:
    run, owner = agent_run
    job = model_workspace(run)
    add_model(run)
    connect_login(client, owner)
    job.workspace.refresh_from_db()
    assert job.workspace.default_agent_connection is None
    assert job_execution(job).backend == AgentBackend.PYDANTIC


def test_working_api_default_keeps_existing_behavior(
    agent_run: tuple[Run, User],
) -> None:
    run, _ = agent_run
    job = model_workspace(run)
    model = add_model(run)
    assert job_execution(job).model == model
    assert build_execution_plan(job)["model"]["connection_id"] == model.pk


def test_connections_changing_never_switch_what_runs_jobs(
    agent_run: tuple[Run, User],
) -> None:
    run, _ = agent_run
    job = run.job
    model = add_model(run)
    # A working model provider does not take over from the chosen login.
    assert job_execution(job).backend == AgentBackend.CLAUDE_CODE
    job = model_workspace(run)
    model.metadata = {"last_check": {"ok": False}}
    model.save(update_fields=["metadata"])
    # A broken model provider does not hand runs to a working login.
    selection = job_execution(job)
    assert selection.backend == AgentBackend.PYDANTIC
    assert selection.problem == (
        "The default model provider Workspace API failed its last check."
    )


def test_workspace_default_is_inherited_and_frozen_in_run_plan(
    agent_run: tuple[Run, User],
) -> None:
    run, owner = agent_run
    connection = run.job.workspace.default_agent_connection
    job = model_workspace(run)
    add_model(run)
    save_execution_preference(job.workspace, f"agent:{connection.pk}", owner)
    plan = build_execution_plan(job)
    assert plan["execution"]["connection_id"] == connection.pk
    assert plan["model"] is None
    save_execution_preference(job.workspace, "model", owner)
    assert job_execution(job).backend == AgentBackend.PYDANTIC
    assert plan["execution"]["connection_id"] == connection.pk


def test_disconnected_explicit_default_never_falls_back_to_api(
    agent_run: tuple[Run, User],
) -> None:
    run, owner = agent_run
    connection = run.job.workspace.default_agent_connection
    job = model_workspace(run)
    add_model(run)
    save_execution_preference(job.workspace, f"agent:{connection.pk}", owner)
    AgentConnection.objects.filter(pk=connection.pk).update(connected=False)
    fresh = Job.objects.get(pk=job.pk)
    plan = build_execution_plan(fresh)
    assert plan["ready"] is False
    assert plan["model"] is None
    assert plan["execution"]["backend"] == "claude_code"


def test_all_jobs_use_workspace_default_and_job_override_api_is_absent(
    agent_run: tuple[Run, User],
    client: Client,
) -> None:
    run, owner = agent_run
    job = run.job
    other = Job.objects.create(
        workspace=job.workspace,
        repository=job.repository,
        key="another",
        name="Another",
        skill_path="SKILL.md",
        config_hash="test",
    )
    assert job_execution(job).connection == job_execution(other).connection
    add_model(run)
    save_execution_preference(job.workspace, "model", owner)
    assert job_execution(job).backend == AgentBackend.PYDANTIC
    assert job_execution(Job.objects.get(pk=other.pk)).backend == AgentBackend.PYDANTIC
    assert not any(
        path.endswith("/jobs/{job_id}/agent")
        for path in api.get_openapi_schema()["paths"]
    )
    assert {"agent_mode", "agent_connection"}.isdisjoint(
        field.name for field in Job._meta.fields
    )


def test_choices_are_the_model_provider_and_your_own_logins(
    agent_run: tuple[Run, User],
) -> None:
    run, owner = agent_run
    workspace = run.job.workspace
    login = workspace.default_agent_connection
    config = execution_configuration(workspace, owner)
    assert config["choices"] == [
        {"key": MODEL_CHOICE, "label": "Model provider", "disabled": False},
        {
            "key": f"agent:{login.pk}",
            "label": "Claude Code subscription · Personal",
            "disabled": False,
        },
    ]
    other = get_user_model().objects.create_user(username="other")
    WorkspaceMembership.objects.create(workspace=workspace, user=other, role="owner")
    # Another owner sees the chosen login but cannot pick it.
    assert execution_configuration(workspace, other)["choices"][1:] == [
        {
            "key": f"agent:{login.pk}",
            "label": "Claude Code subscription · Personal",
            "disabled": True,
        }
    ]


def test_workspace_default_api_validates_ownership_and_permissions(
    agent_run: tuple[Run, User], client: Client
) -> None:
    run, owner = agent_run
    connection = run.job.workspace.default_agent_connection
    client.force_login(owner)
    response = client.post(
        "/api/settings/agent",
        data={"key": f"agent:{connection.pk}"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["selected"] == f"agent:{connection.pk}"
    shown = client.get("/api/settings/agent")
    assert shown.status_code == 200
    assert shown.json()["selected"] == f"agent:{connection.pk}"
    other = get_user_model().objects.create_user(username="other")
    membership = WorkspaceMembership.objects.create(
        workspace=run.job.workspace, user=other, role="owner"
    )
    client.force_login(other)
    response = client.post(
        "/api/settings/agent",
        data={"key": f"agent:{connection.pk}"},
        content_type="application/json",
    )
    assert response.status_code == 422
    membership.role = "admin"
    membership.save(update_fields=["role"])
    response = client.post(
        "/api/settings/agent",
        data={"key": MODEL_CHOICE},
        content_type="application/json",
    )
    assert response.status_code == 403


def test_job_list_resolves_the_workspace_default_once(
    agent_run: tuple[Run, User],
) -> None:
    run, _ = agent_run
    job = run.job
    workspace = job.workspace

    def list_queries() -> int:
        with CaptureQueriesContext(connection) as queries:
            serialize_workspace_jobs(workspace)
        return len(queries)

    one = list_queries()
    for index in range(3):
        Job.objects.create(
            workspace=workspace,
            repository=job.repository,
            key=f"extra-{index}",
            name=f"Extra {index}",
            skill_path="SKILL.md",
            config_hash="test",
        )
    assert list_queries() == one


def test_existing_jobs_keep_model_backend_and_no_subscription_requirement(
    agent_run: tuple[Run, User],
) -> None:
    run, _ = agent_run
    job = model_workspace(run)
    add_model(run)
    plan = build_execution_plan(job)
    assert plan["execution"]["backend"] == "pydantic"
    assert plan["ready"] is True
    assert plan["model"]["connection_name"] == "Workspace API"


def test_subscription_plan_is_ready_without_api_key_and_contains_no_auth(
    agent_run: tuple[Run, User],
) -> None:
    run, _ = agent_run
    assert run.execution_plan["ready"] is True
    assert run.execution_plan["model"] is None
    assert TOKEN not in json.dumps(run.execution_plan)


@pytest.mark.parametrize("selected", [True, False])
def test_run_refusal_reports_the_actual_agent_blocker(
    agent_run: tuple[Run, User], client: Client, selected: bool
) -> None:
    run, owner = agent_run
    run.status = Run.Status.SUCCEEDED
    run.save(update_fields=["status"])
    run.job.repository.enabled = True
    run.job.repository.save(update_fields=["enabled"])
    if selected:
        AgentConnection.objects.filter(
            pk=run.job.workspace.default_agent_connection_id
        ).update(connected=False)
        name = AgentConnection.objects.get(
            pk=run.job.workspace.default_agent_connection_id
        ).display_name
        expected = (
            f"The agent login {name} is disconnected. Reconnect it on Connections."
        )
    else:
        model_workspace(run)
        expected = "No default model provider is connected."
    client.force_login(owner)
    response = client.post(f"/api/jobs/{run.job_id}/run")
    assert response.status_code == 409
    assert response.json()["detail"] == expected


@pytest.mark.parametrize("change", ["not_set_up", "disconnected", "not_owner"])
def test_an_unusable_default_login_says_what_is_wrong(
    agent_run: tuple[Run, User],
    settings: Any,
    change: str,
) -> None:
    run, owner = agent_run
    workspace = run.job.workspace
    login = workspace.default_agent_connection
    if change == "not_set_up":
        settings.AGENT_IMAGE = ""
        expected = "Agent subscriptions are not set up on this server."
    elif change == "disconnected":
        login.connected = False
        login.save(update_fields=["connected"])
        expected = (
            "The agent login Personal is disconnected. Reconnect it on Connections."
        )
    else:
        WorkspaceMembership.objects.filter(workspace=workspace, user=owner).update(
            role=WorkspaceMembership.Role.MEMBER
        )
        expected = "The agent login Personal belongs to someone who is no longer a workspace owner."
    selection = job_execution(run.job)
    assert selection.problem == expected
    assert selection.ready is False
