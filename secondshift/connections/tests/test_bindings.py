from datetime import UTC, datetime

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.connections.bindings import bind, unbind
from secondshift.connections.errors import ConnectionConflict
from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.needs import capability_needs
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.tests.test_needs import make_job
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run
from secondshift.runs.reporting.notifications import notification_targets
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace


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


def slack(workspace: Workspace) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        display_name="Slack",
        auth_strategy=Connection.AuthStrategy.NONE,
        delivery_url="http://localhost:9000/hook",
    )


def notify(job: Job, binding: str) -> CapabilityRequest:
    return CapabilityRequest.objects.create(
        job=job, binding=binding, kind=CapabilityRequest.Kind.NOTIFICATION
    )


def approve(request: CapabilityRequest, connection: Connection) -> JobGrant:
    return JobGrant.objects.create(
        capability_request=request,
        connection=connection,
        status=JobGrant.Status.APPROVED,
    )


def test_one_connection_serves_two_bindings(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    _, workspace, repository = setup
    connection = slack(workspace)
    bind(connection, "engineering-reports")
    bind(connection, "engineering-alerts")
    approve(notify(make_job(repository, "briefing"), "engineering-reports"), connection)
    approve(notify(make_job(repository, "health"), "engineering-alerts"), connection)

    needs = {need.binding: need for need in capability_needs(workspace)}

    assert connection.binding_keys() == ["engineering-alerts", "engineering-reports"]
    assert {need.status for need in needs.values()} == {"ready"}
    assert {need.connection for need in needs.values()} == {connection}


def test_linking_a_binding_does_not_approve_any_job(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    _, workspace, repository = setup
    connection = slack(workspace)
    bind(connection, "engineering-reports")
    notify(make_job(repository, "health"), "engineering-alerts")

    bind(connection, "engineering-alerts")

    need = next(
        need
        for need in capability_needs(workspace)
        if need.binding == "engineering-alerts"
    )
    assert need.status == "needs_approval"
    assert not JobGrant.objects.filter(status=JobGrant.Status.APPROVED).exists()


def test_a_binding_points_to_one_connection(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    _, workspace, _ = setup
    bind(slack(workspace), "engineering-reports")

    with pytest.raises(ConnectionConflict, match="already has a connection"):
        bind(slack(workspace), "engineering-reports")


def test_model_providers_cannot_serve_a_binding(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    _, workspace, _ = setup
    model = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Claude",
    )

    with pytest.raises(ConnectionConflict, match="cannot serve a binding"):
        bind(model, "engineering-reports")


def test_unlinking_a_binding_stops_only_the_jobs_that_used_it(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    _, workspace, repository = setup
    connection = slack(workspace)
    bind(connection, "engineering-reports")
    bind(connection, "engineering-alerts")
    briefing = make_job(repository, "briefing")
    health = make_job(repository, "health")
    kept = approve(notify(briefing, "engineering-reports"), connection)
    dropped = approve(notify(health, "engineering-alerts"), connection)

    unbind(connection, "engineering-alerts")

    kept.refresh_from_db()
    dropped.refresh_from_db()
    assert kept.status == JobGrant.Status.APPROVED
    assert (dropped.status, dropped.connection) == (JobGrant.Status.PENDING, None)
    plan = build_execution_plan(health)
    assert not plan["capabilities"][0]["authorized"]


def test_a_shared_connection_gets_each_report_once(
    setup: tuple[AbstractUser, Workspace, Repository],
) -> None:
    _, workspace, repository = setup
    connection = slack(workspace)
    bind(connection, "engineering-reports")
    bind(connection, "engineering-alerts")
    job = make_job(repository, "briefing")
    approve(notify(job, "engineering-reports"), connection)
    approve(notify(job, "engineering-alerts"), connection)
    run = Run.objects.create(
        job=job,
        commit_sha="abc123",
        scheduled_for=datetime(2026, 10, 1, tzinfo=UTC),
        execution_plan=build_execution_plan(job),
    )

    assert notification_targets(run) == [connection.pk]


def test_owners_link_and_unlink_bindings_through_the_api(
    setup: tuple[AbstractUser, Workspace, Repository], client: Client
) -> None:
    user, workspace, _ = setup
    connection = slack(workspace)
    bind(connection, "engineering-reports")
    client.force_login(user)

    linked = client.post(
        f"/api/connections/{connection.pk}/bindings",
        {"binding": "engineering-alerts"},
        content_type="application/json",
    )
    taken = client.post(
        f"/api/connections/{slack(workspace).pk}/bindings",
        {"binding": "engineering-alerts"},
        content_type="application/json",
    )
    unlinked = client.delete(
        f"/api/connections/{connection.pk}/bindings/engineering-reports"
    )
    missing = client.delete(
        f"/api/connections/{connection.pk}/bindings/engineering-reports"
    )

    assert linked.json()["bindings"] == ["engineering-alerts", "engineering-reports"]
    assert taken.status_code == 409
    assert unlinked.json()["bindings"] == ["engineering-alerts"]
    assert missing.status_code == 404


def test_only_owners_change_which_bindings_a_connection_serves(
    setup: tuple[AbstractUser, Workspace, Repository], client: Client
) -> None:
    _, workspace, _ = setup
    connection = slack(workspace)
    bind(connection, "engineering-reports")
    admin = get_user_model().objects.create_user(username="admin")
    WorkspaceMembership.objects.create(
        workspace=workspace, user=admin, role=WorkspaceMembership.Role.ADMIN
    )
    client.force_login(admin)

    linked = client.post(
        f"/api/connections/{connection.pk}/bindings",
        {"binding": "engineering-alerts"},
        content_type="application/json",
    )
    unlinked = client.delete(
        f"/api/connections/{connection.pk}/bindings/engineering-reports"
    )

    assert (linked.status_code, unlinked.status_code) == (403, 403)
    assert connection.binding_keys() == ["engineering-reports"]


def test_a_revoked_connection_cannot_take_a_binding(
    setup: tuple[AbstractUser, Workspace, Repository], client: Client
) -> None:
    user, workspace, _ = setup
    connection = slack(workspace)
    connection.status = Connection.Status.REVOKED
    connection.save()
    client.force_login(user)

    response = client.post(
        f"/api/connections/{connection.pk}/bindings",
        {"binding": "engineering-alerts"},
        content_type="application/json",
    )

    assert response.status_code == 409
