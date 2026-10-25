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
from django.db.models import QuerySet
from django.utils import timezone

from secondshift.workspaces.models import (
    Workspace,
    WorkspaceInvite,
    WorkspaceMembership,
)

INVITE_LIFETIME = timedelta(days=7)
INVITE_UNUSABLE_MESSAGE = (
    "This invitation has expired or was already used. "
    "Ask a workspace owner for a new one."
)


class InviteError(Exception):
    pass


@dataclass(frozen=True)
class NewInvite:
    invite: WorkspaceInvite
    # Only here, right after creating it. It is never stored.
    token: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def usable_invites() -> QuerySet[WorkspaceInvite]:
    return WorkspaceInvite.objects.filter(
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    )


def create_invite(
    workspace: Workspace,
    role: str,
    created_by: AbstractBaseUser | None,
    note: str = "",
) -> NewInvite:
    token = secrets.token_urlsafe(32)
    invite = WorkspaceInvite.objects.create(
        workspace=workspace,
        role=WorkspaceMembership.Role(role),
        note=note,
        token_hash=token_hash(token),
        created_by=created_by,
        expires_at=timezone.now() + INVITE_LIFETIME,
    )
    return NewInvite(invite, token)


def find_invite(token: str) -> WorkspaceInvite | None:
    """The invite for a token while it can still be accepted."""
    return (
        usable_invites()
        .select_related("workspace", "created_by")
        .filter(token_hash=token_hash(token))
        .first()
    )


def pending_invites(workspace: Workspace) -> QuerySet[WorkspaceInvite]:
    return usable_invites().filter(workspace=workspace)


@transaction.atomic
def accept_invite(token: str, user: AbstractBaseUser) -> WorkspaceMembership:
    """Add the person to the invite's workspace and use the invite up.

    Someone who already belongs keeps their role, and the link stays unused.
    """
    invite = (
        usable_invites()
        .select_for_update()
        .filter(token_hash=token_hash(token))
        .first()
    )
    if invite is None:
        raise InviteError(INVITE_UNUSABLE_MESSAGE)
    existing = WorkspaceMembership.objects.filter(
        workspace_id=invite.workspace_id, user_id=user.pk
    ).first()
    if existing:
        return existing
    membership = WorkspaceMembership.objects.create(
        workspace_id=invite.workspace_id, user=user, role=invite.role
    )
    invite.accepted_by = user
    invite.accepted_at = timezone.now()
    invite.save(update_fields=["accepted_by", "accepted_at"])
    return membership


def revoke_invite(invite: WorkspaceInvite) -> None:
    invite.revoked_at = timezone.now()
    invite.save(update_fields=["revoked_at"])
