"""The first account on a single-host install and the workspace it owns."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser

from secondshift.core.services import create_workspace, workspace_owner
from secondshift.workspaces.models import Workspace, WorkspaceMembership

WORKSPACE_SLUG = "secondshift"
WORKSPACE_NAME = "SecondShift"
# The owner's username when `secondshift up` creates it without asking.
LOCAL_OWNER_USERNAME = "owner"


def has_owner() -> bool:
    return WorkspaceMembership.objects.filter(
        workspace__slug=WORKSPACE_SLUG, role=WorkspaceMembership.Role.OWNER
    ).exists()


def get_owner() -> AbstractUser:
    return workspace_owner(Workspace.objects.get(slug=WORKSPACE_SLUG))  # type: ignore[return-value]


def ensure_owner(email: str = "", password: str | None = None) -> Workspace:
    """Create the owner and the workspace when they do not exist yet.

    Without a password the owner can only sign in with a link from
    `secondshift up` or `secondshift login`.
    """
    user, created = get_user_model().objects.get_or_create(
        username=email or LOCAL_OWNER_USERNAME,
        defaults={"email": email, "is_staff": True, "is_superuser": True},
    )
    if created:
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
    workspace = Workspace.objects.filter(slug=WORKSPACE_SLUG).first()
    if workspace is None:
        return create_workspace(WORKSPACE_NAME, WORKSPACE_SLUG, user)
    WorkspaceMembership.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMembership.Role.OWNER},
    )
    return workspace


def set_owner_credentials(email: str, password: str) -> None:
    """Let the owner sign in with an email and password too."""
    user = get_owner()
    user.email = email
    user.set_password(password)
    user.save()
