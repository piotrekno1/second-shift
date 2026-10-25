from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from secondshift.workspaces.access import ROLE_RANK, Role
from secondshift.workspaces.models import Workspace, WorkspaceMembership

LAST_OWNER_MESSAGE = "A workspace needs at least one owner."


class MembershipError(Exception):
    pass


@transaction.atomic
def create_workspace(name: str, slug: str, owner: AbstractBaseUser) -> Workspace:
    """A new workspace, with its creator as its owner."""
    workspace = Workspace.objects.create(name=name, slug=slug)
    WorkspaceMembership.objects.create(
        workspace=workspace, user=owner, role=WorkspaceMembership.Role.OWNER
    )
    return workspace


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
