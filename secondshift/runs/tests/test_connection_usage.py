from decimal import Decimal

import pytest
from django.test import Client

from secondshift.connections.bindings import bind
from secondshift.connections.models import Connection
from secondshift.gateway.models import ModelUsage
from secondshift.jobs.models import CapabilityRequest, JobGrant
from secondshift.runs.models import Run
from secondshift.runs.reporting.connection_usage import connection_usage
from secondshift.runs.tests.test_health import NOW, add_run, add_usage
from secondshift.runs.tests.test_run_queries import make_job, make_workspace
from secondshift.workspaces.services import workspace_owner


def make_connection(workspace: object, key: str, kind: str) -> Connection:
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=kind,
        display_name=key,
        auth_strategy=Connection.AuthStrategy.NONE,
    )
    if kind == Connection.Kind.NOTIFICATION:
        bind(connection, key)
    return connection


def grant(job: object, connection: Connection, status: str) -> None:
    capability = CapabilityRequest.objects.create(
        job=job, binding=connection.display_name, kind="notification"
    )
    JobGrant.objects.create(
        capability_request=capability, connection=connection, status=status
    )


@pytest.mark.django_db
def test_usage_counts_approved_jobs_and_this_weeks_model_spend() -> None:
    workspace = make_workspace("team")
    alerts = make_connection(workspace, "alerts", Connection.Kind.NOTIFICATION)
    model = make_connection(workspace, "model", Connection.Kind.MODEL)
    unused = make_connection(workspace, "unused", Connection.Kind.NOTIFICATION)
    first, second = (
        make_job(workspace, "acme/api", "a"),
        make_job(workspace, "acme/api", "b"),
    )
    grant(first, alerts, JobGrant.Status.APPROVED)
    grant(second, alerts, JobGrant.Status.PENDING)
    run = add_run(first, Run.Status.SUCCEEDED, 60)
    old = add_run(first, Run.Status.SUCCEEDED, 60 * 24 * 10)
    add_usage(run, "0.0200", days_ago=0.1)
    add_usage(run, None, days_ago=0.1)
    add_usage(old, "5.0000", days_ago=10)
    ModelUsage.objects.update(connection=model)

    usage = connection_usage(workspace, now=NOW)

    assert usage[alerts.pk].jobs == 1 and usage[alerts.pk].last_used_at is None
    assert (
        usage[model.pk].runs_7d,
        usage[model.pk].spend_7d_usd,
        usage[model.pk].spend_partial,
    ) == (1, Decimal("0.0200"), True)
    assert usage[model.pk].last_used_at is not None
    assert unused.pk not in usage


@pytest.mark.django_db
def test_connections_api_includes_usage(client: Client) -> None:
    workspace = make_workspace("team")
    alerts = make_connection(workspace, "alerts", Connection.Kind.NOTIFICATION)
    grant(make_job(workspace, "acme/api", "a"), alerts, JobGrant.Status.APPROVED)
    client.force_login(workspace_owner(workspace))

    [body] = client.get("/api/connections").json()

    assert body["usage"] == {
        "jobs": 1,
        "runs_7d": 0,
        "spend_7d_usd": 0,
        "spend_partial": False,
        "last_used_at": None,
    }
