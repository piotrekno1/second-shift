import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings

from secondshift.workspaces.models import ApiToken, Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace
from secondshift.workspaces.tokens import ApiTokenAuth, create_api_token

Role = WorkspaceMembership.Role
TOKENS = "/api/account/api-tokens"


def user(name: str) -> AbstractUser:
    return get_user_model().objects.create_user(username=name, email=f"{name}@x.dev")


def join(workspace: Workspace, person: AbstractUser, role: str) -> WorkspaceMembership:
    return WorkspaceMembership.objects.create(
        workspace=workspace, user=person, role=role
    )


def create(client: Client, workspace: Workspace, **body: object) -> HttpResponse:
    body = {
        "workspace_id": workspace.pk,
        "name": "Laptop",
        "expires_in_days": 30,
        **body,
    }
    return client.post(TOKENS, data=json.dumps(body), content_type="application/json")


def signed(secret: str) -> HttpRequest:
    """A request carrying the token, after bearer auth has run."""
    request = RequestFactory().post("/mcp", HTTP_AUTHORIZATION=f"Bearer {secret}")
    request.user = get_user_model()()
    request.auth = ApiTokenAuth()(request)
    return request


@pytest.fixture
def owner(db: object) -> AbstractUser:
    person = user("owner")
    create_workspace("Acme", "acme", person)
    return person


@pytest.fixture
def acme(owner: AbstractUser) -> Workspace:
    return Workspace.objects.get(slug="acme")


def membership(workspace: Workspace, person: AbstractUser) -> WorkspaceMembership:
    return WorkspaceMembership.objects.get(workspace=workspace, user=person)


def test_the_secret_is_shown_once_and_only_its_hash_is_stored(
    client: Client, acme: Workspace, owner: AbstractUser
) -> None:
    client.force_login(owner)

    created = create(client, acme, scope="run")

    assert created.status_code == 200
    body = created.json()
    secret = body["token"]
    assert secret.startswith("ss_")
    assert (body["name"], body["scope"], body["workspace_slug"]) == (
        "Laptop",
        "run",
        "acme",
    )
    assert secret not in ApiToken.objects.get().token_hash
    listed = client.get(TOKENS).json()
    assert [item["id"] for item in listed["items"]] == [body["id"]]
    assert secret not in json.dumps(listed)


def test_the_list_covers_every_workspace_and_offers_run_only_where_allowed(
    client: Client, acme: Workspace, owner: AbstractUser
) -> None:
    beta = create_workspace("Beta", "beta", user("beta-owner"))
    join(beta, owner, Role.MEMBER)
    create_api_token(membership(acme, owner), "Acme laptop", "run", 30)
    create_api_token(membership(beta, owner), "Beta laptop", "read", 30)
    client.force_login(owner)

    listed = client.get(TOKENS).json()

    assert {(item["name"], item["workspace_slug"]) for item in listed["items"]} == {
        ("Acme laptop", "acme"),
        ("Beta laptop", "beta"),
    }
    assert [
        (item["slug"], item["current"], item["can_create_run_tokens"])
        for item in listed["workspaces"]
    ] == [("acme", True, True), ("beta", False, False)]
    refused = create(client, beta, scope="run")
    assert refused.status_code == 422
    assert "owners and admins" in refused.json()["detail"]
    assert create(client, beta, scope="read").status_code == 200


def test_tokens_cannot_be_created_for_another_workspace(
    client: Client, owner: AbstractUser
) -> None:
    other = create_workspace("Other", "other", user("stranger"))
    client.force_login(owner)

    assert create(client, other).status_code == 404
    assert not ApiToken.objects.exists()


@override_settings(API_TOKEN_MAX_DAYS=30)
@pytest.mark.parametrize("days", [0, 31])
def test_expiry_must_be_within_the_server_maximum(
    client: Client, acme: Workspace, owner: AbstractUser, days: int
) -> None:
    client.force_login(owner)

    response = create(client, acme, expires_in_days=days)

    assert response.status_code == 422
    assert "between 1 and 30 days" in response.json()["detail"]
    assert client.get(TOKENS).json()["max_days"] == 30


def test_people_see_and_revoke_only_their_own_tokens(
    client: Client, acme: Workspace, owner: AbstractUser
) -> None:
    person = user("member")
    join(acme, person, Role.MEMBER)
    mine = create_api_token(membership(acme, owner), "Owner's", "read", 30)
    theirs = create_api_token(membership(acme, person), "Member's", "read", 30)

    client.force_login(owner)
    listed = client.get(TOKENS).json()["items"]
    assert [item["name"] for item in listed] == ["Owner's"]
    assert client.delete(f"{TOKENS}/{theirs.token.pk}").status_code == 404
    assert client.delete(f"{TOKENS}/{mine.token.pk}").status_code == 204
    assert signed(mine.secret).auth is None
    assert signed(theirs.secret).auth == theirs.token


def test_tokens_from_a_workspace_the_person_left_are_not_listed(
    client: Client, acme: Workspace, owner: AbstractUser
) -> None:
    beta = create_workspace("Beta", "beta", user("beta-owner"))
    join(beta, owner, Role.ADMIN)
    create_api_token(membership(beta, owner), "Beta laptop", "run", 30)
    membership(beta, owner).delete()
    client.force_login(owner)

    assert client.get(TOKENS).json()["items"] == []


def test_tokens_cannot_manage_tokens(acme: Workspace, owner: AbstractUser) -> None:
    new = create_api_token(membership(acme, owner), "Laptop", "run", 30)

    response = create(Client(HTTP_AUTHORIZATION=f"Bearer {new.secret}"), acme)

    assert response.status_code == 401
    assert ApiToken.objects.count() == 1
