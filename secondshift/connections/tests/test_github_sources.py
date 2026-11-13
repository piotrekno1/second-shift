from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from secondshift.connections import github
from secondshift.connections.github import connection_source
from secondshift.connections.models import Connection
from secondshift.github import credentials
from secondshift.github.errors import GitHubError
from secondshift.github.tokens import TokenOwner
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace, WorkspaceMembership

TOKEN = "github_pat_" + "a" * 40


@pytest.fixture
def workspace(db: object) -> Workspace:
    user = get_user_model().objects.create_user(username="owner")
    return create_workspace(name="Test", slug="test", owner=user)


@pytest.fixture
def owner_client(workspace: Workspace) -> Client:
    client = Client()
    client.force_login(get_user_model().objects.get(username="owner"))
    return client


@pytest.fixture
def token_owner(monkeypatch: pytest.MonkeyPatch) -> Mock:
    inspect = Mock(return_value=TokenOwner("someone", None))
    monkeypatch.setattr(github, "inspect_token", inspect)
    return inspect


def test_owners_connect_a_token_that_is_never_returned(
    owner_client: Client, workspace: Workspace, token_owner: Mock
) -> None:
    response = owner_client.post(
        "/api/connections/github-token",
        {"token": TOKEN},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert TOKEN not in response.content.decode()
    assert response.json()["auth"] == "token"
    connection = Connection.objects.get(workspace=workspace)
    assert connection.kind == Connection.Kind.SOURCE
    assert connection.auth_strategy == Connection.AuthStrategy.API_KEY
    assert connection.access_token == TOKEN
    assert connection.external_account_id == "token:someone"


def test_only_owners_connect_tokens(
    owner_client: Client, workspace: Workspace, token_owner: Mock
) -> None:
    membership = workspace.memberships.get()
    membership.role = WorkspaceMembership.Role.ADMIN
    membership.save()
    response = owner_client.post(
        "/api/connections/github-token",
        {"token": TOKEN},
        content_type="application/json",
    )
    assert response.status_code == 403
    token_owner.assert_not_called()


def test_a_refused_token_is_not_stored(
    owner_client: Client, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        github, "inspect_token", Mock(side_effect=GitHubError("GitHub refused it."))
    )
    response = owner_client.post(
        "/api/connections/github-token",
        {"token": TOKEN},
        content_type="application/json",
    )
    assert response.status_code == 422
    assert not Connection.objects.exists()


def test_a_new_token_for_the_same_account_replaces_the_old_one(
    workspace: Workspace, token_owner: Mock
) -> None:
    first = github.connect_token_source(workspace, TOKEN)
    first.status = Connection.Status.REVOKED
    first.access_token = ""
    first.save()
    second = github.connect_token_source(workspace, "github_pat_" + "b" * 40)
    assert second.pk == first.pk
    assert second.status == Connection.Status.CONNECTED
    assert second.access_token.endswith("b" * 40)


def test_replacing_a_connection_keeps_it(
    owner_client: Client, workspace: Workspace, token_owner: Mock
) -> None:
    first = github.connect_token_source(workspace, TOKEN)
    response = owner_client.post(
        "/api/connections/github-token",
        {"token": "github_pat_" + "b" * 40, "connection_id": first.pk},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["id"] == first.pk
    first.refresh_from_db()
    assert first.access_token.endswith("b" * 40)


def test_replacing_refuses_a_token_from_another_account(
    owner_client: Client, workspace: Workspace, token_owner: Mock
) -> None:
    first = github.connect_token_source(workspace, TOKEN)
    token_owner.return_value = TokenOwner("someone-else", None)
    response = owner_client.post(
        "/api/connections/github-token",
        {"token": "github_pat_" + "b" * 40, "connection_id": first.pk},
        content_type="application/json",
    )
    assert response.status_code == 422
    assert "someone-else" in response.json()["detail"]
    assert Connection.objects.count() == 1
    first.refresh_from_db()
    assert first.access_token == TOKEN


def test_replacing_needs_a_token_connection(
    owner_client: Client, workspace: Workspace, token_owner: Mock
) -> None:
    installation = github.Installation("42", "someone")
    (app,) = github.link_installations(workspace, [installation])
    response = owner_client.post(
        "/api/connections/github-token",
        {"token": TOKEN, "connection_id": app.pk},
        content_type="application/json",
    )
    assert response.status_code == 404
    token_owner.assert_not_called()


def source(workspace: Workspace, **values: Any) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.API_KEY,
        external_account_id="token:someone",
        display_name="GitHub",
        access_token=TOKEN,
        **values,
    )


def test_a_token_source_gives_its_token(workspace: Workspace) -> None:
    found = connection_source(source(workspace))
    assert found == credentials.PersonalToken(TOKEN)
    assert credentials.token_for(found, 7, credentials.WRITE) == TOKEN


def test_an_expired_token_says_so(workspace: Workspace) -> None:
    expired = source(workspace, expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(GitHubError, match="expired"):
        connection_source(expired)


def test_the_dashboard_sees_an_expired_token_as_expired(
    owner_client: Client, workspace: Workspace
) -> None:
    source(workspace, expires_at=timezone.now() - timedelta(minutes=1))

    listed = owner_client.get("/api/connections").json()

    assert [item["status"] for item in listed] == ["expired"]


@pytest.mark.parametrize(
    ("strategy", "status", "minutes", "expected"),
    [
        (Connection.AuthStrategy.API_KEY, Connection.Status.CONNECTED, -1, "expired"),
        (Connection.AuthStrategy.API_KEY, Connection.Status.CONNECTED, 60, "connected"),
        (
            Connection.AuthStrategy.API_KEY,
            Connection.Status.CONNECTED,
            None,
            "connected",
        ),
        (Connection.AuthStrategy.API_KEY, Connection.Status.REVOKED, -1, "revoked"),
        # An OAuth token refreshes when used, so its expiry is no problem yet.
        (Connection.AuthStrategy.OAUTH, Connection.Status.CONNECTED, -1, "connected"),
    ],
)
def test_current_status_counts_tokens_that_cannot_refresh(
    strategy: str, status: str, minutes: int | None, expected: str
) -> None:
    expires_at = (
        None if minutes is None else timezone.now() + timedelta(minutes=minutes)
    )
    connection = Connection(
        auth_strategy=strategy, status=status, expires_at=expires_at
    )

    assert connection.current_status == expected


def test_a_revoked_token_source_is_disconnected(workspace: Workspace) -> None:
    revoked = source(workspace, status=Connection.Status.REVOKED)
    with pytest.raises(GitHubError, match="disconnected"):
        connection_source(revoked)


def github_source(workspace: Workspace, auth: str) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=auth,
        display_name="GitHub · ada",
        external_account_id="token:ada"
        if auth == Connection.AuthStrategy.API_KEY
        else "99",
    )


def attribution(client: Client, connection: Connection, on: bool) -> Any:
    return client.post(
        f"/api/connections/{connection.pk}/attribution",
        {"attribution": on},
        content_type="application/json",
    )


@pytest.mark.parametrize(
    ("auth", "shown"),
    [(Connection.AuthStrategy.MANAGED, True), (Connection.AuthStrategy.API_KEY, False)],
)
def test_attribution_is_on_for_the_app_and_off_for_a_token(
    owner_client: Client, workspace: Workspace, auth: str, shown: bool
) -> None:
    connection = github_source(workspace, auth)

    [listed] = owner_client.get("/api/connections").json()

    assert listed["id"] == connection.pk
    assert listed["attribution"] is shown


def test_owners_turn_attribution_on_for_a_token(
    owner_client: Client, workspace: Workspace
) -> None:
    connection = github_source(workspace, Connection.AuthStrategy.API_KEY)

    response = attribution(owner_client, connection, True)

    assert response.status_code == 200
    assert response.json()["attribution"] is True
    connection.refresh_from_db()
    assert connection.attribution is True
    assert attribution(owner_client, connection, False).json()["attribution"] is False


def test_attribution_is_only_for_github_sources(
    owner_client: Client, workspace: Workspace
) -> None:
    webhook = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        display_name="Alerts",
    )

    assert attribution(owner_client, webhook, True).status_code == 404
    webhook.refresh_from_db()
    assert webhook.attribution is None
