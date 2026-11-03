from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from secondshift.connections import github
from secondshift.connections.models import Connection
from secondshift.github import credentials
from secondshift.github.errors import GitHubError
from secondshift.github.tokens import TokenOwner
from secondshift.repositories.github_access import connection_source
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace

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


def test_a_revoked_token_source_is_disconnected(workspace: Workspace) -> None:
    revoked = source(workspace, status=Connection.Status.REVOKED)
    with pytest.raises(GitHubError, match="disconnected"):
        connection_source(revoked)
