import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.workspaces.invites import create_invite, invite_path, revoke_invite
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace


def user(name: str) -> AbstractUser:
    return get_user_model().objects.create_user(username=name, email=f"{name}@x.dev")


@pytest.fixture
def workspace(db: object) -> Workspace:
    return create_workspace("Acme", "acme", user("owner"))


def open_link_then_sign_in(client: Client, token: str, person: AbstractUser) -> None:
    client.get(invite_path(token))
    client.force_login(person)


def test_signing_in_after_a_link_asks_before_joining(
    client: Client, workspace: Workspace
) -> None:
    new = create_invite(workspace, "admin", workspace.memberships.get().user)
    person = user("person")
    create_workspace("Own", "own", person)
    open_link_then_sign_in(client, new.token, person)

    pending = client.get("/api/invites/pending").json()

    assert pending == {
        "workspace_name": "Acme",
        "role": "admin",
        "invited_by": "owner@x.dev",
    }
    assert not workspace.memberships.filter(user=person).exists()

    joined = client.post("/api/invites/pending/accept")

    assert joined.status_code == 200
    assert (joined.json()["slug"], joined.json()["role"]) == ("acme", "admin")
    me = client.get("/api/me").json()
    assert (me["workspace_slug"], me["role"]) == ("acme", "admin")
    assert client.get("/api/invites/pending").json() is None


def test_dismissing_forgets_the_invite_without_joining(
    client: Client, workspace: Workspace
) -> None:
    new = create_invite(workspace, "member", None)
    person = user("person")
    open_link_then_sign_in(client, new.token, person)

    assert client.post("/api/invites/pending/dismiss").status_code == 204

    assert client.get("/api/invites/pending").json() is None
    assert client.post("/api/invites/pending/accept").status_code == 409
    assert not WorkspaceMembership.objects.filter(user=person).exists()


def test_a_revoked_invite_cannot_be_accepted_later(
    client: Client, workspace: Workspace
) -> None:
    new = create_invite(workspace, "member", None)
    person = user("person")
    open_link_then_sign_in(client, new.token, person)
    revoke_invite(new.invite)

    assert client.get("/api/invites/pending").json() is None
    accepted = client.post("/api/invites/pending/accept")

    assert accepted.status_code == 409
    assert "expired or was already used" in accepted.json()["detail"]
    assert not WorkspaceMembership.objects.filter(user=person).exists()


def test_members_are_not_asked_to_join_again(
    client: Client, workspace: Workspace
) -> None:
    new = create_invite(workspace, "admin", None)
    owner = workspace.memberships.get().user
    open_link_then_sign_in(client, new.token, owner)

    assert client.get("/api/invites/pending").json() is None
    assert workspace.memberships.get(user=owner).role == "owner"


def test_pending_invites_need_sign_in(client: Client, workspace: Workspace) -> None:
    new = create_invite(workspace, "member", None)
    client.get(invite_path(new.token))

    assert client.get("/api/invites/pending").status_code == 401
    assert client.post("/api/invites/pending/accept").status_code == 401
