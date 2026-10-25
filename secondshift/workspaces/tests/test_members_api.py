import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.http import HttpResponse
from django.test import Client

from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace


def user(name: str) -> AbstractUser:
    return get_user_model().objects.create_user(username=name, email=f"{name}@x.dev")


def membership(workspace: Workspace, person: AbstractUser) -> WorkspaceMembership:
    return workspace.memberships.get(user=person)


def set_role(client: Client, item: WorkspaceMembership, role: str) -> HttpResponse:
    return client.post(
        f"/api/members/{item.pk}/role",
        data=json.dumps({"role": role}),
        content_type="application/json",
    )


@pytest.fixture
def team(db: object) -> dict[str, object]:
    owner, admin, member = user("owner"), user("admin"), user("member")
    workspace = create_workspace("Acme", "acme", owner)
    workspace.memberships.create(user=admin, role="admin")
    workspace.memberships.create(user=member, role="member")
    return {"workspace": workspace, "owner": owner, "admin": admin, "member": member}


def test_everyone_sees_members_owners_first(client: Client, team: dict) -> None:
    client.force_login(team["member"])

    listed = client.get("/api/members").json()

    assert [(item["email"], item["role"], item["you"]) for item in listed] == [
        ("owner@x.dev", "owner", False),
        ("admin@x.dev", "admin", False),
        ("member@x.dev", "member", True),
    ]


@pytest.mark.django_db
def test_an_owner_who_joined_later_still_comes_first(client: Client) -> None:
    early, founder = user("early"), user("founder")
    workspace = create_workspace("Team", "team", early)
    workspace.memberships.filter(user=early).update(role="member")
    workspace.memberships.create(user=founder, role="owner")
    client.force_login(founder)

    members = client.get("/api/members").json()

    assert [member["role"] for member in members] == ["owner", "member"]


def test_owners_change_roles_and_remove_people(client: Client, team: dict) -> None:
    workspace = team["workspace"]
    client.force_login(team["owner"])

    promoted = set_role(client, membership(workspace, team["member"]), "admin")
    removed = client.delete(f"/api/members/{membership(workspace, team['admin']).pk}")

    assert promoted.json()["role"] == "admin"
    assert removed.status_code == 204
    assert sorted(workspace.memberships.values_list("role", flat=True)) == [
        "admin",
        "owner",
    ]


@pytest.mark.parametrize("role", ["admin", "member"])
def test_only_owners_manage_members(client: Client, team: dict, role: str) -> None:
    workspace = team["workspace"]
    client.force_login(team[role])
    target = membership(workspace, team["member" if role == "admin" else "admin"])

    assert set_role(client, target, "owner").status_code == 403
    assert client.delete(f"/api/members/{target.pk}").status_code == 403


def test_the_last_owner_stays(client: Client, team: dict) -> None:
    workspace = team["workspace"]
    owner = membership(workspace, team["owner"])
    client.force_login(team["owner"])

    demoted = set_role(client, owner, "admin")
    removed = client.delete(f"/api/members/{owner.pk}")

    assert demoted.status_code == 409
    assert demoted.json()["detail"] == "A workspace needs at least one owner."
    assert removed.status_code == 409
    # With a second owner, the first can step down.
    set_role(client, membership(workspace, team["admin"]), "owner")
    assert set_role(client, owner, "member").status_code == 200


def test_members_of_other_workspaces_are_out_of_reach(
    client: Client, team: dict
) -> None:
    stranger = user("stranger")
    theirs = create_workspace("Other", "other", stranger)
    client.force_login(team["owner"])

    target = membership(theirs, stranger)

    assert set_role(client, target, "member").status_code == 404
    assert client.delete(f"/api/members/{target.pk}").status_code == 404
