"""The first account on a single-host install and the workspace it owns."""

from django.contrib.auth.models import AbstractUser

from secondshift.workspaces.dao import (
    create_workspace,
    ensure_membership,
    find_workspace_by_slug,
    get_or_create_user,
    get_workspace_by_slug,
    workspace_owner,
    workspace_slug_has_owner,
)
from secondshift.workspaces.models import Workspace, WorkspaceMembership

WORKSPACE_SLUG = "secondshift"
WORKSPACE_NAME = "SecondShift"
# The owner's username when `secondshift up` creates it without asking.
LOCAL_OWNER_USERNAME = "owner"


def has_owner() -> bool:
    return workspace_slug_has_owner(WORKSPACE_SLUG)


def get_owner() -> AbstractUser:
    return workspace_owner(get_workspace_by_slug(WORKSPACE_SLUG))  # type: ignore[return-value]


def ensure_owner(email: str = "", password: str | None = None) -> Workspace:
    """Create the owner and the workspace when they do not exist yet.

    Without a password the owner can only sign in with a link from
    `secondshift up` or `secondshift login`.
    """
    user, created = get_or_create_user(
        email or LOCAL_OWNER_USERNAME,
        {"email": email, "is_staff": True, "is_superuser": True},
    )
    if created:
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
    workspace = find_workspace_by_slug(WORKSPACE_SLUG)
    if workspace is None:
        return create_workspace(WORKSPACE_NAME, WORKSPACE_SLUG, user)
    ensure_membership(workspace, user, WorkspaceMembership.Role.OWNER)
    return workspace


def set_owner_credentials(email: str, password: str) -> None:
    """Let the owner sign in with an email and password too."""
    user = get_owner()
    user.email = email
    user.set_password(password)
    user.save()
