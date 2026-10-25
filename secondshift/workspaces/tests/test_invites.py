from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.utils import timezone

from secondshift.workspaces.invites import (
    InviteError,
    accept_invite,
    create_invite,
    find_invite,
    pending_invites,
    revoke_invite,
    token_hash,
)
from secondshift.workspaces.models import (
    Workspace,
    WorkspaceInvite,
    WorkspaceMembership,
)
from secondshift.workspaces.services import create_workspace


def user(name: str) -> AbstractUser:
    return get_user_model().objects.create_user(username=name)


@pytest.fixture
def workspace(db: object) -> Workspace:
    return create_workspace("Team", "team", user("owner"))


def test_only_a_hash_of_the_token_is_stored(workspace: Workspace) -> None:
    new = create_invite(workspace, "admin", None, note="Ana")

    stored = WorkspaceInvite.objects.get()
    assert stored.token_hash == token_hash(new.token)
    assert new.token not in stored.token_hash
    assert find_invite(new.token) == stored
    assert find_invite("not-a-token") is None


def test_accepting_adds_the_person_with_the_role_once(workspace: Workspace) -> None:
    new = create_invite(workspace, "admin", None)
    person = user("person")

    membership = accept_invite(new.token, person)

    assert (membership.workspace, membership.role) == (workspace, "admin")
    new.invite.refresh_from_db()
    assert new.invite.accepted_by == person
    assert list(pending_invites(workspace)) == []
    with pytest.raises(InviteError, match="expired or was already used"):
        accept_invite(new.token, user("second"))


def test_members_keep_their_role_and_the_link_stays_unused(
    workspace: Workspace,
) -> None:
    new = create_invite(workspace, "member", None)
    owner = workspace.memberships.get().user

    membership = accept_invite(new.token, owner)

    assert membership.role == "owner"
    assert list(pending_invites(workspace)) == [new.invite]


def test_expired_and_revoked_invites_cannot_be_used(workspace: Workspace) -> None:
    expired = create_invite(workspace, "member", None)
    WorkspaceInvite.objects.filter(pk=expired.invite.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    revoked = create_invite(workspace, "member", None)
    revoke_invite(revoked.invite)

    for token in (expired.token, revoked.token):
        assert find_invite(token) is None
        with pytest.raises(InviteError):
            accept_invite(token, user(f"late-{token[:4]}"))
    assert list(pending_invites(workspace)) == []
    assert WorkspaceMembership.objects.count() == 1


def test_an_unknown_role_is_refused(workspace: Workspace) -> None:
    with pytest.raises(ValueError):
        create_invite(workspace, "superuser", None)
