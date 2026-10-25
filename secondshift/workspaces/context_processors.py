from django.http import HttpRequest

from secondshift.workspaces.invites import pending_invite
from secondshift.workspaces.models import WorkspaceInvite


def invite(request: HttpRequest) -> dict[str, WorkspaceInvite | None]:
    """The invite this browser opened, so sign-in pages can name it."""
    if not hasattr(request, "session"):
        return {"pending_invite": None}
    return {"pending_invite": pending_invite(request)}
