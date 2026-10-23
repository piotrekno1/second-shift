"""The first account on a single-host install and the workspace it owns."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser

from secondshift.core.models import Workspace, WorkspaceMembership

WORKSPACE_SLUG = "secondshift"
# The owner's username when `secondshift up` creates it without asking.
LOCAL_OWNER_USERNAME = "owner"


def has_owner() -> bool:
    return get_user_model().objects.exists()


def get_owner() -> AbstractUser:
    return Workspace.objects.select_related("owner").get(slug=WORKSPACE_SLUG).owner


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
    workspace, _ = Workspace.objects.get_or_create(
        slug=WORKSPACE_SLUG,
        defaults={"name": "SecondShift", "owner": user},
    )
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
