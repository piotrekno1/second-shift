from django.contrib.auth.models import AbstractBaseUser

from secondshift.core.models import Workspace


def workspace_for_user(user: AbstractBaseUser) -> Workspace | None:
    owned = Workspace.objects.filter(owner=user).first()
    if owned:
        return owned
    return (
        Workspace.objects.filter(memberships__user=user)
        .order_by("memberships__created_at")
        .first()
    )
