import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from secondshift.workspaces.invites import (
    PENDING_INVITE_SESSION_KEY,
    create_invite,
    invite_path,
    revoke_invite,
)
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace


@pytest.fixture
def workspace(db: object) -> Workspace:
    owner = get_user_model().objects.create_user(username="owner")
    return create_workspace("Team", "team", owner)


def test_opening_a_link_signed_out_leads_to_sign_up(
    client: Client, workspace: Workspace
) -> None:
    new = create_invite(workspace, "member", None)

    response = client.get(invite_path(new.token))

    assert response.status_code == 302
    assert response["Location"] == "/accounts/signup/"
    assert client.session[PENDING_INVITE_SESSION_KEY] == new.token
    # Sign-up is closed on this server, but open to someone with an invite.
    signup = client.get("/accounts/signup/")
    assert signup.status_code == 200
    assert b"Sign Up Closed" not in signup.content


def test_opening_a_link_signed_in_does_not_join_yet(
    client: Client, workspace: Workspace
) -> None:
    new = create_invite(workspace, "admin", None)
    person = get_user_model().objects.create_user(username="person")
    client.force_login(person)

    response = client.get(invite_path(new.token))

    assert response.status_code == 302
    assert response["Location"] == "/"
    assert not WorkspaceMembership.objects.filter(user=person).exists()


def test_an_unusable_link_says_so_and_keeps_sign_up_closed(
    client: Client, workspace: Workspace
) -> None:
    new = create_invite(workspace, "member", None)
    revoke_invite(new.invite)

    response = client.get(invite_path(new.token))

    assert response.status_code == 400
    assert b"expired or was already used" in response.content
    assert PENDING_INVITE_SESSION_KEY not in client.session
    assert b"Sign Up Closed" in client.get("/accounts/signup/").content


def test_a_link_revoked_after_opening_closes_sign_up_again(
    client: Client, workspace: Workspace
) -> None:
    new = create_invite(workspace, "member", None)
    client.get(invite_path(new.token))

    revoke_invite(new.invite)

    assert b"Sign Up Closed" in client.get("/accounts/signup/").content


@pytest.mark.parametrize("page", ["/accounts/signup/", "/accounts/login/"])
def test_sign_in_pages_name_the_workspace_being_joined(
    client: Client, workspace: Workspace, page: str
) -> None:
    new = create_invite(workspace, "admin", None)
    client.get(invite_path(new.token))

    content = client.get(page).content.decode()

    assert "join <strong>Team</strong> as Admin" in content


def test_sign_in_pages_say_nothing_without_an_invite(
    client: Client, db: object
) -> None:
    assert "account-invite" not in client.get("/accounts/login/").content.decode()
