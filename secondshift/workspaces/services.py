from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils.text import slugify

from secondshift.workspaces.access import ROLE_RANK, Role
from secondshift.workspaces.models import Workspace, WorkspaceMembership

LAST_OWNER_MESSAGE = "A workspace needs at least one owner."
# How many workspaces someone who is not staff may own, so open sign-up
# cannot be used to fill the database.
OWNED_WORKSPACE_LIMIT = 3


class MembershipError(Exception):
    pass


class WorkspaceCreationError(Exception):
    pass


class WorkspaceCreationRefused(WorkspaceCreationError):
    """The person may not create one, as opposed to asking for a bad one."""


@transaction.atomic
def create_workspace(name: str, slug: str, owner: AbstractBaseUser) -> Workspace:
    """A new workspace, with its creator as its owner."""
    workspace = Workspace.objects.create(name=name, slug=slug)
    WorkspaceMembership.objects.create(
        workspace=workspace, user=owner, role=WorkspaceMembership.Role.OWNER
    )
    return workspace


def can_create_workspace(user: AbstractBaseUser) -> bool:
    """Anyone may when sign-up is open. Otherwise only staff, who run the server."""
    return bool(settings.SIGNUP_ENABLED or getattr(user, "is_staff", False))


def free_slug(name: str) -> str:
    """A slug from the name, with a number added when it is taken."""
    base = slugify(name)[:40].strip("-") or "workspace"
    slug, number = base, 1
    while Workspace.objects.filter(slug=slug).exists():
        number += 1
        slug = f"{base}-{number}"
    return slug


def create_workspace_for(user: AbstractBaseUser, name: str) -> Workspace:
    """A workspace someone made for themselves from the dashboard."""
    if not can_create_workspace(user):
        raise WorkspaceCreationRefused(
            "Only server staff can create workspaces. Ask an owner for an invite."
        )
    name = name.strip()
    if not name:
        raise WorkspaceCreationError("Give the workspace a name.")
    owned = WorkspaceMembership.objects.filter(user=user, role=Role.OWNER).count()
    if not getattr(user, "is_staff", False) and owned >= OWNED_WORKSPACE_LIMIT:
        raise WorkspaceCreationRefused(
            f"You can own up to {OWNED_WORKSPACE_LIMIT} workspaces."
        )
    return create_workspace(name, free_slug(name), user)


def workspace_owner(workspace: Workspace) -> AbstractBaseUser:
    """The workspace's first owner. Raises when it has none."""
    return (
        workspace.memberships.select_related("user")
        .filter(role=WorkspaceMembership.Role.OWNER)
        .earliest("created_at", "pk")
        .user
    )


def members_in_order(workspace: Workspace) -> list[WorkspaceMembership]:
    """Owners first, then admins, then members, each in the order they joined."""
    return sorted(
        workspace.memberships.select_related("user").order_by("created_at", "pk"),
        key=lambda membership: -ROLE_RANK[Role(membership.role)],
    )


def ensure_another_owner(membership: WorkspaceMembership) -> None:
    """Refuse to take away the workspace's last owner."""
    if membership.role != Role.OWNER:
        return
    owners = (
        WorkspaceMembership.objects.select_for_update()
        .filter(workspace_id=membership.workspace_id, role=Role.OWNER)
        .exclude(pk=membership.pk)
    )
    if not owners.exists():
        raise MembershipError(LAST_OWNER_MESSAGE)


@transaction.atomic
def change_role(membership: WorkspaceMembership, role: str) -> WorkspaceMembership:
    if Role(role) != Role.OWNER:
        ensure_another_owner(membership)
    membership.role = Role(role)
    membership.save(update_fields=["role"])
    return membership


@transaction.atomic
def remove_member(membership: WorkspaceMembership) -> None:
    ensure_another_owner(membership)
    membership.delete()
