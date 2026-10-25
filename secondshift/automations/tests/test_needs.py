import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.automations.models import (
    CapabilityRequest,
    Job,
    JobGrant,
    Run,
    RunEvent,
)
from secondshift.automations.needs import capability_needs
from secondshift.automations.notifications import notification_targets
from secondshift.automations.planning import build_execution_plan
from secondshift.automations.workflows import notify
from secondshift.core.services import create_workspace
from secondshift.integrations import webhooks
from secondshift.integrations.models import Connection
from secondshift.repositories.models import Repository
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
        job=job,
        status=Run.Status.FAILED,
        commit_sha="abc123",
        scheduled_for=datetime(2026, 10, 1, tzinfo=UTC),
        summary="No isolated runner service is connected.",
        execution_plan=build_execution_plan(job),
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
