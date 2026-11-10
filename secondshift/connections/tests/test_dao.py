import pytest
from django.contrib.auth import get_user_model
from django.db import connection as database

from secondshift.connections.dao import (
    auto_approved_connections,
    bound_connections,
    connection_for_binding,
    connections_by_binding,
)
from secondshift.connections.models import Connection
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace


@pytest.fixture
def workspace(db: object) -> Workspace:
    user = get_user_model().objects.create_user(username="owner")
    return create_workspace(name="Test", slug="test", owner=user)


def make_connection(
    workspace: Workspace, binding_key: str, **fields: object
) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key=binding_key,
        display_name=binding_key or "unbound",
        status=Connection.Status.CONNECTED,
        **fields,
    )


def test_bound_connections_skip_unbound_and_other_workspaces(
    workspace: Workspace,
) -> None:
    github = make_connection(workspace, "github")
    sentry = make_connection(workspace, "sentry")
    make_connection(workspace, "")
    owner = get_user_model().objects.create_user(username="other")
    other = create_workspace(name="Other", slug="other", owner=owner)
    make_connection(other, "github")

    assert set(bound_connections(workspace.pk)) == {github, sentry}
    assert list(bound_connections(workspace.pk, ["sentry"])) == [sentry]
    assert connections_by_binding(workspace.pk) == {
        "github": github,
        "sentry": sentry,
    }


def test_connection_for_binding(workspace: Workspace) -> None:
    github = make_connection(workspace, "github")

    assert connection_for_binding(workspace.pk, "github") == github
    assert connection_for_binding(workspace.pk, "sentry") is None
    assert connection_for_binding(workspace.pk, "") is None


def test_auto_approved_connections_need_no_review(workspace: Workspace) -> None:
    open_connection = make_connection(workspace, "github", require_approval=False)
    make_connection(workspace, "sentry", require_approval=True)

    assert list(auto_approved_connections({workspace.pk}, {"github", "sentry"})) == [
        open_connection
    ]


def test_connection_secrets_are_stored_encrypted(workspace: Workspace) -> None:
    secrets = {
        "access_token": "ghp_access-secret",
        "refresh_token": "refresh-secret",
        "delivery_url": "https://hooks.example/T000/secret-path",
    }
    saved = make_connection(workspace, "github", **secrets)

    loaded = Connection.objects.get(pk=saved.pk)
    assert {name: getattr(loaded, name) for name in secrets} == secrets
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT access_token, refresh_token, delivery_url "
            "FROM connections_connection WHERE id = %s",
            [saved.pk],
        )
        stored = " ".join(cursor.fetchone())
    assert not [value for value in secrets.values() if value in stored]
