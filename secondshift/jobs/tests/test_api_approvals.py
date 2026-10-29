import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client
from django.utils import timezone

from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace


@pytest.fixture
def approval_setup(db: object) -> tuple[AbstractUser, Workspace, Job]:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="local/project",
        source_type=Repository.SourceType.LOCAL,
        local_path="/tmp/project",
        clone_url="https://github.com/local/project.git",
        enabled=True,
        config_sha="abc123",
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        timezone="UTC",
        config_hash="config-hash",
    )
    Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        status=Connection.Status.CONNECTED,
        allowed_models=["claude-opus-5-5"],
        default_model="claude-opus-5-5",
        is_default=True,
    )
    return user, workspace, job


@pytest.mark.django_db
def test_optional_unconfigured_capability_does_not_require_approval(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
) -> None:
    user, _, job = approval_setup
    CapabilityRequest.objects.create(
        job=job,
        binding="engineering-alerts",
        kind=CapabilityRequest.Kind.NOTIFICATION,
        required=False,
    )
    client.force_login(user)

    response = client.get("/api/jobs")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["grant_status"] == "not_required"
    assert payload["capabilities"][0]["grant_status"] == "unconfigured"


@pytest.mark.django_db
@pytest.mark.parametrize("existing_grant", [False, True])
def test_owner_can_approve_required_capability_and_enable_job(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
    existing_grant: bool,
) -> None:
    user, workspace, job = approval_setup
    capability = CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics"],
        required=True,
    )
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key="observability",
        display_name="Local Grafana",
        status=Connection.Status.CONNECTED,
        available_tools=["query_metrics"],
    )
    if existing_grant:
        JobGrant.objects.create(
            capability_request=capability,
            connection=connection,
            allowed_tools=["query_metrics"],
        )
    client.force_login(user)

    blocked = client.post(
        f"/api/jobs/{job.pk}/enabled",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )
    reviewed = client.post(
        f"/api/jobs/{job.pk}/capabilities/{capability.pk}/grant",
        data=json.dumps(
            {
                "decision": "approved",
                "connection_id": connection.pk,
                "allowed_tools": ["query_metrics"],
            }
        ),
        content_type="application/json",
    )
    enabled = client.post(
        f"/api/jobs/{job.pk}/enabled",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == (
        "Connect a default model and approve all required capabilities first."
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["grant_status"] == "approved"
    grant = JobGrant.objects.get(capability_request=capability)
    assert grant.connection == connection
    assert grant.reviewed_by == user
    assert grant.allowed_tools == ["query_metrics"]
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    run = Run.objects.create(
        job=job,
        commit_sha=job.repository.config_sha,
        scheduled_for=timezone.now(),
        execution_plan={"ready": True},
    )
    detail = client.get(f"/api/jobs/{job.pk}")
    runs = client.get("/api/runs")

    assert detail.status_code == 200
    assert detail.json()["repository_id"] == job.repository_id
    assert detail.json()["recent_runs"][0]["id"] == run.pk
    assert runs.json()["items"][0]["job_id"] == job.pk
    assert runs.json()["items"][0]["repository_id"] == job.repository_id


@pytest.mark.django_db
def test_approval_rejects_connection_that_does_not_match_binding(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
) -> None:
    user, workspace, job = approval_setup
    capability = CapabilityRequest.objects.create(
        job=job,
        binding="observability",
        kind=CapabilityRequest.Kind.MCP,
        requested_tools=["query_metrics"],
    )
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key="other-service",
        display_name="Other MCP",
        available_tools=["query_metrics"],
    )
    client.force_login(user)

    response = client.post(
        f"/api/jobs/{job.pk}/capabilities/{capability.pk}/grant",
        data=json.dumps({"decision": "approved", "connection_id": connection.pk}),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert "must match" in response.json()["detail"]


@pytest.mark.django_db
def test_owner_can_deny_capability_without_a_connection(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
) -> None:
    user, _, job = approval_setup
    capability = CapabilityRequest.objects.create(
        job=job,
        binding="production-api",
        kind=CapabilityRequest.Kind.API,
        requested_tools=["deploy"],
    )
    client.force_login(user)

    response = client.post(
        f"/api/jobs/{job.pk}/capabilities/{capability.pk}/grant",
        data=json.dumps({"decision": "denied", "connection_id": None}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["grant_status"] == "denied"
    grant = JobGrant.objects.get(capability_request=capability)
    assert grant.connection is None
    assert grant.allowed_tools == []


@pytest.mark.django_db
def test_workspace_member_cannot_review_or_enable_jobs(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
) -> None:
    _, workspace, job = approval_setup
    member = get_user_model().objects.create_user(username="member")
    WorkspaceMembership.objects.create(
        workspace=workspace,
        user=member,
        role=WorkspaceMembership.Role.MEMBER,
    )
    capability = CapabilityRequest.objects.create(
        job=job,
        binding="production-api",
        kind=CapabilityRequest.Kind.API,
        requested_tools=["deploy"],
    )
    client.force_login(member)

    reviewed = client.post(
        f"/api/jobs/{job.pk}/capabilities/{capability.pk}/grant",
        data=json.dumps({"decision": "denied"}),
        content_type="application/json",
    )
    enabled = client.post(
        f"/api/jobs/{job.pk}/enabled",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert reviewed.status_code == 403
    assert enabled.status_code == 403


@pytest.mark.django_db
def test_run_now_works_while_the_schedule_is_off(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, job = approval_setup
    queued: list[int] = []
    monkeypatch.setattr(
        "secondshift.runs.services.enqueue_run", lambda run: queued.append(run.pk)
    )
    client.force_login(user)

    response = client.post(f"/api/jobs/{job.pk}/run")

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.enabled is False
    assert queued == [response.json()["id"]]


@pytest.mark.django_db
def test_run_now_needs_an_enabled_repository(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, job = approval_setup
    Repository.objects.filter(pk=job.repository_id).update(enabled=False)
    monkeypatch.setattr("secondshift.runs.services.enqueue_run", lambda run: None)
    client.force_login(user)

    response = client.post(f"/api/jobs/{job.pk}/run")

    assert response.status_code == 409
    assert response.json()["detail"] == "Enable the repository before running its jobs."
    assert not Run.objects.filter(job=job).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status", Run.ACTIVE_STATUSES)
def test_run_now_refuses_while_a_run_is_active_and_names_it(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    user, _, job = approval_setup
    queued: list[int] = []
    monkeypatch.setattr(
        "secondshift.runs.services.enqueue_run", lambda run: queued.append(run.pk)
    )
    client.force_login(user)
    first = client.post(f"/api/jobs/{job.pk}/run").json()
    Run.objects.filter(pk=first["id"]).update(status=status)

    response = client.post(f"/api/jobs/{job.pk}/run")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "This job already has a queued or running run.",
        "run_id": first["id"],
    }
    assert queued == [first["id"]]
    assert Run.objects.filter(job=job).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", [Run.Status.SUCCEEDED, Run.Status.FAILED, Run.Status.CANCELED]
)
def test_run_now_works_again_once_the_last_run_finished(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    user, _, job = approval_setup
    monkeypatch.setattr("secondshift.runs.services.enqueue_run", lambda run: None)
    client.force_login(user)
    first = client.post(f"/api/jobs/{job.pk}/run").json()
    Run.objects.filter(pk=first["id"]).update(status=status)

    response = client.post(f"/api/jobs/{job.pk}/run")

    assert response.status_code == 200
    assert response.json()["id"] != first["id"]


@pytest.mark.django_db
def test_job_without_a_schedule_cannot_be_enabled(
    client: Client,
    approval_setup: tuple[AbstractUser, Workspace, Job],
) -> None:
    user, _, job = approval_setup
    Job.objects.filter(pk=job.pk).update(cron="")
    client.force_login(user)

    response = client.post(
        f"/api/jobs/{job.pk}/enabled",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This job has no schedule. Use Run now to run it."
    )
