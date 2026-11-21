import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.connections import webhooks
from secondshift.connections.models import Connection
from secondshift.jobs import capability_needs as capability_needs_module
from secondshift.jobs.capability_needs import capability_needs
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.planner import build_execution_plan
from secondshift.repositories.models import Repository
from secondshift.runs.constants import RunStart
from secondshift.runs.dao import create_task
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.orchestration.workflows import notify
from secondshift.runs.reporting.notifications import notification_targets
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace


@pytest.fixture
def setup(db: object) -> tuple[AbstractUser, Workspace, Repository]:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="local/project",
        source_type=Repository.SourceType.LOCAL,
        local_path="/tmp/project",
        enabled=True,
        config_sha="abc123",
    )
    return user, workspace, repository


def make_job(repository: Repository, key: str) -> Job:
    return Job.objects.create(
        workspace=repository.workspace,
        repository=repository,
        key=key,
        name=key.title(),
        skill_path=f".agents/skills/{key}/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )


def notify_request(job: Job, required: bool = True) -> CapabilityRequest:
    return CapabilityRequest.objects.create(
        job=job,
        binding="engineering-alerts",
        kind=CapabilityRequest.Kind.NOTIFICATION,
        required=required,
    )


def alerts_webhook(workspace: Workspace) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        binding_key="engineering-alerts",
        display_name="Alerts",
        auth_strategy=Connection.AuthStrategy.NONE,
        delivery_url="http://localhost:9000/hook",
    )


@pytest.mark.django_db
def test_needs_group_jobs_by_binding(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    _, workspace, repository = setup
    notify_request(make_job(repository, "health"), required=False)
    notify_request(make_job(repository, "triage"), required=True)

    needs = capability_needs(workspace)

    assert len(needs) == 1
    assert needs[0].binding == "engineering-alerts"
    assert needs[0].status == "missing"
    assert needs[0].required is True
    assert [job.job_name for job in needs[0].jobs] == ["Health", "Triage"]


@pytest.mark.django_db
def test_needs_progress_from_connected_to_ready(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    user, workspace, repository = setup
    capability = notify_request(make_job(repository, "health"))
    connection = alerts_webhook(workspace)

    assert capability_needs(workspace)[0].status == "needs_approval"

    JobGrant.objects.create(
        capability_request=capability,
        connection=connection,
        status=JobGrant.Status.APPROVED,
        reviewed_by=user,
    )
    assert capability_needs(workspace)[0].status == "ready"

    connection.status = Connection.Status.REVOKED
    connection.save()
    assert capability_needs(workspace)[0].status == "unavailable"


@pytest.mark.django_db
def test_needs_flag_binding_used_by_other_kind(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    _, workspace, repository = setup
    notify_request(make_job(repository, "health"))
    Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.MCP,
        kind=Connection.Kind.MCP,
        binding_key="engineering-alerts",
        display_name="Wrong kind",
    )

    need = capability_needs(workspace)[0]

    assert need.status == "unavailable"
    assert "not notification" in need.detail


@pytest.mark.django_db
def test_needs_endpoint_excludes_removed_repositories(
    client: Client,
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    user, _, repository = setup
    notify_request(make_job(repository, "health"))
    repository.removed_at = datetime(2026, 1, 1, tzinfo=UTC)
    repository.save()
    client.force_login(user)

    response = client.get("/api/capability-needs")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.django_db
def test_finished_run_notifies_only_still_approved_webhooks(
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    user, workspace, repository = setup
    job = make_job(repository, "health")
    capability = notify_request(job)
    connection = alerts_webhook(workspace)
    grant = JobGrant.objects.create(
        capability_request=capability,
        connection=connection,
        status=JobGrant.Status.APPROVED,
        reviewed_by=user,
    )
    run = Run.objects.create(
        task=create_task(repository=job.repository),
        started_by=RunStart.SCHEDULE,
        job=job,
        status=Run.Status.FAILED,
        commit_sha="abc123",
        scheduled_for=datetime(2026, 10, 1, tzinfo=UTC),
        summary="No isolated runner service is connected.",
        execution_plan=build_execution_plan(job).stored(),
    )
    sent: list[dict[str, Any]] = []

    def receive(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    monkeypatch.setattr(
        webhooks, "webhook_transport", lambda: httpx.MockTransport(receive)
    )

    notify(run.pk)

    assert sent[0]["event"] == "run.finished"
    assert sent[0]["run"]["status"] == "failed"
    assert "Health failed" in sent[0]["text"]
    assert RunEvent.objects.filter(run=run, level="info").exists()

    grant.status = JobGrant.Status.DENIED
    grant.save()
    assert notification_targets(run) == []


@pytest.mark.django_db
def test_a_binding_some_repositories_cannot_use_names_them(
    setup: tuple[AbstractUser, Workspace, Repository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, local = setup
    other = Repository.objects.create(
        workspace=workspace,
        full_name="local/other",
        source_type=Repository.SourceType.LOCAL,
        local_path="/tmp/other",
        enabled=True,
    )
    on_github = Repository.objects.create(
        workspace=workspace, full_name="acme/web", external_id="7", enabled=True
    )
    for repository in (local, other, on_github):
        CapabilityRequest.objects.create(
            job=make_job(repository, "review"),
            binding="github",
            kind=CapabilityRequest.Kind.MCP,
            requested_tools=["list_commits"],
        )
    Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key="github",
        display_name="GitHub tools",
        available_tools=["list_commits"],
    )

    def access(connection: Connection, ref: Any) -> SimpleNamespace:
        on_github_ref = ref.github_name is not None
        return SimpleNamespace(ok=on_github_ref, text="GitHub tools need GitHub.")

    monkeypatch.setattr(capability_needs_module, "external_access", access)
    monkeypatch.setattr(
        capability_needs_module,
        "repository_ref",
        lambda repository: SimpleNamespace(
            github_name=repository.full_name if repository.external_id else None
        ),
    )

    need = capability_needs(workspace)[0]

    assert need.status == "unavailable"
    assert need.detail == (
        "Jobs in other and project can't use it. GitHub tools need GitHub."
    )
