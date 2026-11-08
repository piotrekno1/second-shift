"""One-time links that let someone join a workspace with a role.

An owner creates an invite and shares its link. The token appears only in
that link. The database keeps a hash of it. A link works once, until it
expires or an owner revokes it. Joining always needs the person to confirm
while signed in, so opening a link alone never adds anyone to a workspace.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from secondshift.workspaces.dao import (
    create_invite_record,
    create_membership,
    find_membership,
    find_usable_invite,
    get_or_create_workspace,
    lock_usable_invite,
    workspace_has_owner,
)
from secondshift.workspaces.models import (
    Workspace,
    WorkspaceInvite,
    WorkspaceMembership,
)

INVITE_LIFETIME = timedelta(days=7)
# An opened invite link waits in the session under this key until the person
# confirms or dismisses it.
PENDING_INVITE_SESSION_KEY = "secondshift.invite"
INVITE_UNUSABLE_MESSAGE = (
    "This invitation has expired or was already used. "
    "Ask a workspace owner for a new one."
)


class InviteError(Exception):
    pass


class WorkspaceTaken(Exception):
    pass


@dataclass(frozen=True)
class NewInvite:
    invite: WorkspaceInvite
    # Only here, right after creating it. It is never stored.
    token: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invite_path(token: str) -> str:
    return reverse("invite", args=[token])


def create_invite(
    workspace: Workspace,
    role: str,
    created_by: AbstractBaseUser | None,
    note: str = "",
) -> NewInvite:
    token = secrets.token_urlsafe(32)
    invite = create_invite_record(
        workspace,
        WorkspaceMembership.Role(role),
        note,
        token_hash(token),
        created_by,
        timezone.now() + INVITE_LIFETIME,
    )
    return NewInvite(invite, token)


def find_invite(token: str) -> WorkspaceInvite | None:
    """The invite for a token while it can still be accepted."""
    return find_usable_invite(token_hash(token))


@transaction.atomic
def accept_invite(token: str, user: AbstractBaseUser) -> WorkspaceMembership:
    """Add the person to the invite's workspace and use the invite up.

    Someone who already belongs keeps their role, and the link stays unused.
    """
    invite = lock_usable_invite(token_hash(token))
    if invite is None:
        raise InviteError(INVITE_UNUSABLE_MESSAGE)
    existing = find_membership(invite.workspace_id, user.pk)
    if existing:
        return existing
    membership = create_membership(invite.workspace_id, user, invite.role)
    invite.accepted_by = user
    invite.accepted_at = timezone.now()
    invite.save(update_fields=["accepted_by", "accepted_at"])
    return membership


def revoke_invite(invite: WorkspaceInvite) -> None:
    invite.revoked_at = timezone.now()
    invite.save(update_fields=["revoked_at"])


def remember_invite(request: HttpRequest, token: str) -> None:
    request.session[PENDING_INVITE_SESSION_KEY] = token


def pending_invite(request: HttpRequest) -> WorkspaceInvite | None:
    """The invite this browser opened, while it can still be accepted."""
    token = request.session.get(PENDING_INVITE_SESSION_KEY)
    return find_invite(token) if token else None


def forget_invite(request: HttpRequest) -> None:
    request.session.pop(PENDING_INVITE_SESSION_KEY, None)


def join_pending_invite(request: HttpRequest) -> WorkspaceMembership:
    """Accept the invite waiting in the session, for the signed-in person."""
    token = request.session.get(PENDING_INVITE_SESSION_KEY)
    forget_invite(request)
    if not token:
        raise InviteError(INVITE_UNUSABLE_MESSAGE)
    return accept_invite(token, request.user)


@transaction.atomic
def invite_first_owner(name: str, slug: str) -> NewInvite:
    """An owner invite for a new workspace, or one that has no owner yet.

    This is how a server gets its first owner without any account existing.
    """
    workspace = get_or_create_workspace(slug, name)
    if workspace_has_owner(workspace.pk):
        raise WorkspaceTaken(
            f"{workspace.name} already has an owner. Owners invite people "
            "from the Settings page."
        )
    return create_invite(workspace, WorkspaceMembership.Role.OWNER, None)
