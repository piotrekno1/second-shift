import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.http import HttpResponse
from django.test import Client

from secondshift.workspaces.models import Workspace, WorkspaceInvite
from secondshift.workspaces.services import create_workspace


def user(name: str) -> AbstractUser:
    return get_user_model().objects.create_user(username=name, email=f"{name}@x.dev")


def invite(client: Client, **body: str) -> HttpResponse:
    return client.post(
        "/api/invites", data=json.dumps(body), content_type="application/json"
    )


@pytest.fixture
def owner(db: object) -> AbstractUser:
    person = user("owner")
    create_workspace("Acme", "acme", person)
    return person


def test_owners_get_a_link_once_and_see_pending_invites(
    client: Client, owner: AbstractUser
) -> None:
    client.force_login(owner)

    created = invite(client, role="admin", note="Ana")

    assert created.status_code == 200
    body = created.json()
    assert body["link"].startswith("http://testserver/-/invite/")
    assert (body["role"], body["note"], body["created_by"]) == (
        "admin",
        "Ana",
        "owner@x.dev",
    )
    listed = client.get("/api/invites").json()
    assert [(item["id"], item["note"]) for item in listed] == [(body["id"], "Ana")]
    token = body["link"].rstrip("/").rsplit("/", 1)[1]
    assert token not in json.dumps(listed)
    assert token not in WorkspaceInvite.objects.get().token_hash

    # The link works for the person it was sent to.
    newcomer = Client()
    newcomer.get(body["link"].removeprefix("http://testserver"))
    newcomer.force_login(user("ana"))
    assert newcomer.post("/api/invites/pending/accept").json()["role"] == "admin"
    assert client.get("/api/invites").json() == []


def test_revoking_stops_the_link(client: Client, owner: AbstractUser) -> None:
    client.force_login(owner)
    body = invite(client).json()

    assert client.delete(f"/api/invites/{body['id']}").status_code == 204

    assert client.get("/api/invites").json() == []
    opened = Client().get(body["link"].removeprefix("http://testserver"))
    assert opened.status_code == 400
    assert client.delete(f"/api/invites/{body['id']}").status_code == 404


@pytest.mark.parametrize("role", ["admin", "member"])
def test_only_owners_invite_people(
    client: Client, owner: AbstractUser, role: str
) -> None:
    workspace = Workspace.objects.get(slug="acme")
    person = user(role)
    workspace.memberships.create(user=person, role=role)
    client.force_login(person)

    assert invite(client).status_code == 403
    assert client.get("/api/invites").status_code == 403


def test_invites_stay_in_their_workspace(client: Client, owner: AbstractUser) -> None:
    client.force_login(owner)
    theirs = invite(client).json()
    other = user("other")
    create_workspace("Other", "other", other)
    client.force_login(other)

    assert client.get("/api/invites").json() == []
    assert client.delete(f"/api/invites/{theirs['id']}").status_code == 404


def test_an_unknown_role_is_refused(client: Client, owner: AbstractUser) -> None:
    client.force_login(owner)

    assert invite(client, role="superuser").status_code == 422
