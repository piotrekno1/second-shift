from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from secondshift.core.models import Workspace, WorkspaceMembership


@transaction.atomic
def create_workspace(name: str, slug: str, owner: AbstractBaseUser) -> Workspace:
    """A new workspace, with its creator as its owner."""
    workspace = Workspace.objects.create(name=name, slug=slug, owner=owner)
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


def workspace_for_user(user: AbstractBaseUser) -> Workspace | None:
    owned = Workspace.objects.filter(owner=user).first()
    if owned:
        return owned
    return (
        Workspace.objects.filter(memberships__user=user)
        .order_by("memberships__created_at")
        .first()
    )
