"""Queries and writes on people, workspaces, memberships, invites and API tokens."""

from datetime import datetime
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from secondshift.workspaces.models import (
    ApiToken,
    Workspace,
    WorkspaceInvite,
    WorkspaceMembership,
)

Role = WorkspaceMembership.Role


# People


def active_user(user_id: object) -> AbstractBaseUser | None:
    """The active account with this id, if there is one."""
    return get_user_model().objects.filter(pk=user_id, is_active=True).first()


def get_or_create_user(
    username: str, defaults: dict[str, Any]
) -> tuple[AbstractBaseUser, bool]:
    return get_user_model().objects.get_or_create(username=username, defaults=defaults)


# Workspaces


@transaction.atomic
def create_workspace(name: str, slug: str, owner: AbstractBaseUser) -> Workspace:
    """A new workspace, with this person as its owner."""
    workspace = Workspace.objects.create(name=name, slug=slug)
    WorkspaceMembership.objects.create(workspace=workspace, user=owner, role=Role.OWNER)
    return workspace


def get_or_create_workspace(slug: str, name: str) -> Workspace:
    workspace, _ = Workspace.objects.get_or_create(slug=slug, defaults={"name": name})
    return workspace


def get_workspace_by_slug(slug: str) -> Workspace:
    """The workspace. Raises Workspace.DoesNotExist when there is none."""
    return Workspace.objects.get(slug=slug)


def find_workspace_by_slug(slug: str) -> Workspace | None:
    return Workspace.objects.filter(slug=slug).first()


def slug_taken(slug: str) -> bool:
    return Workspace.objects.filter(slug=slug).exists()


# Memberships


def workspace_has_owner(workspace_id: int) -> bool:
    return WorkspaceMembership.objects.filter(
        workspace_id=workspace_id, role=Role.OWNER
    ).exists()


def workspace_slug_has_owner(slug: str) -> bool:
    return WorkspaceMembership.objects.filter(
        workspace__slug=slug, role=Role.OWNER
    ).exists()


def workspace_owner(workspace: Workspace) -> AbstractBaseUser:
    """The workspace's first owner. Raises when it has none."""
    return (
        WorkspaceMembership.objects.select_related("user")
        .filter(workspace=workspace, role=Role.OWNER)
        .earliest("created_at", "pk")
        .user
    )


def is_owner(workspace_id: int, user_id: int) -> bool:
    return WorkspaceMembership.objects.filter(
        workspace_id=workspace_id, user_id=user_id, role=Role.OWNER
    ).exists()


def find_membership(workspace_id: int, user_id: int) -> WorkspaceMembership | None:
    return WorkspaceMembership.objects.filter(
        workspace_id=workspace_id, user_id=user_id
    ).first()


def active_member_role(workspace_id: int, user_id: int | None) -> str | None:
    """The person's role in the workspace while their account is active."""
    return (
        WorkspaceMembership.objects.filter(
            workspace_id=workspace_id, user_id=user_id, user__is_active=True
        )
        .values_list("role", flat=True)
        .first()
    )


def membership_in(user_id: int, workspace_id: int) -> WorkspaceMembership | None:
    return memberships_for_user(user_id).filter(workspace_id=workspace_id).first()


def first_membership(user_id: int) -> WorkspaceMembership | None:
    return memberships_for_user(user_id).first()


def memberships_for_user(user_id: int) -> QuerySet[WorkspaceMembership]:
    """The person's memberships with their workspaces, the first one joined first."""
    return (
        WorkspaceMembership.objects.filter(user_id=user_id)
        .select_related("workspace")
        .order_by("created_at", "pk")
    )


def workspace_members(workspace: Workspace) -> QuerySet[WorkspaceMembership]:
    """The workspace's memberships with their people, in the order they joined."""
    return (
        WorkspaceMembership.objects.filter(workspace=workspace)
        .select_related("user")
        .order_by("created_at", "pk")
    )


def create_membership(
    workspace_id: int, user: AbstractBaseUser, role: str
) -> WorkspaceMembership:
    return WorkspaceMembership.objects.create(
        workspace_id=workspace_id, user=user, role=role
    )


def ensure_membership(
    workspace: Workspace, user: AbstractBaseUser, role: str
) -> WorkspaceMembership:
    """The person's membership, created with this role when missing."""
    membership, _ = WorkspaceMembership.objects.get_or_create(
        workspace=workspace, user=user, defaults={"role": role}
    )
    return membership


def owned_workspace_count(user: AbstractBaseUser) -> int:
    return WorkspaceMembership.objects.filter(user=user, role=Role.OWNER).count()


def lock_other_owners_exist(membership: WorkspaceMembership) -> bool:
    """Lock the workspace's other owners and say whether there are any."""
    return (
        WorkspaceMembership.objects.select_for_update()
        .filter(workspace_id=membership.workspace_id, role=Role.OWNER)
        .exclude(pk=membership.pk)
        .exists()
    )


# Invites


def usable_invites() -> QuerySet[WorkspaceInvite]:
    """Invites that were not accepted, revoked or left to expire."""
    return WorkspaceInvite.objects.filter(
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    )


def pending_invites(workspace: Workspace) -> QuerySet[WorkspaceInvite]:
    """Invites that can still be accepted, with who sent them."""
    return usable_invites().filter(workspace=workspace).select_related("created_by")


def find_usable_invite(token_hash: str) -> WorkspaceInvite | None:
    """The usable invite with this hash, with its workspace and inviter."""
    return (
        usable_invites()
        .select_related("workspace", "created_by")
        .filter(token_hash=token_hash)
        .first()
    )


def lock_usable_invite(token_hash: str) -> WorkspaceInvite | None:
    """Lock the usable invite with this hash for this transaction."""
    return usable_invites().select_for_update().filter(token_hash=token_hash).first()


def create_invite_record(
    workspace: Workspace,
    role: str,
    note: str,
    token_hash: str,
    created_by: AbstractBaseUser | None,
    expires_at: datetime,
) -> WorkspaceInvite:
    return WorkspaceInvite.objects.create(
        workspace=workspace,
        role=role,
        note=note,
        token_hash=token_hash,
        created_by=created_by,
        expires_at=expires_at,
    )


# API tokens


def usable_tokens() -> QuerySet[ApiToken]:
    """Tokens that were not revoked and have not expired."""
    return ApiToken.objects.filter(
        revoked_at__isnull=True, expires_at__gt=timezone.now()
    )


def has_own_token(user: AbstractBaseUser, workspace: Workspace) -> bool:
    return own_tokens(user).filter(workspace=workspace).exists()


def own_tokens(user: AbstractBaseUser) -> QuerySet[ApiToken]:
    """The person's usable tokens, in workspaces they still belong to."""
    return (
        usable_tokens()
        .filter(
            created_by_id=user.pk,
            created_by__workspace_memberships__workspace_id=F("workspace_id"),
        )
        .select_related("workspace")
    )


def api_token_by_hash(token_hash: str) -> ApiToken | None:
    """The usable token with this hash, while its creator is an active member."""
    return (
        usable_tokens()
        .filter(
            token_hash=token_hash,
            created_by__is_active=True,
            created_by__workspace_memberships__workspace_id=F("workspace_id"),
        )
        .select_related("created_by", "workspace")
        .first()
    )


def create_token_record(
    membership: WorkspaceMembership,
    name: str,
    scope: str,
    token_hash: str,
    expires_at: datetime,
) -> ApiToken:
    return ApiToken.objects.create(
        workspace_id=membership.workspace_id,
        created_by_id=membership.user_id,
        name=name,
        scope=scope,
        token_hash=token_hash,
        expires_at=expires_at,
    )


def touch_token(token: ApiToken, now: datetime, stale_before: datetime) -> None:
    """Set the token's last use, unless it was already set after `stale_before`."""
    stale = Q(last_used_at__isnull=True) | Q(last_used_at__lt=stale_before)
    ApiToken.objects.filter(stale, pk=token.pk).update(last_used_at=now)
