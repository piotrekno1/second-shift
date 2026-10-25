from django.http import HttpRequest
from ninja import Router, Schema, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.workspaces.access import (
    current_workspace,
    select_workspace,
    user_memberships,
)
from secondshift.workspaces.invites import (
    InviteError,
    forget_invite,
    join_pending_invite,
    pending_invite,
)
from secondshift.workspaces.models import WorkspaceMembership

router = Router(auth=django_auth, tags=["workspaces"])


class WorkspaceChoiceSchema(Schema):
    id: int
    name: str
    slug: str
    role: str
    current: bool


class WorkspaceSelectSchema(Schema):
    workspace_id: int


class PendingInviteSchema(Schema):
    workspace_name: str
    role: str
    invited_by: str | None


def serialize_choice(
    membership: WorkspaceMembership, current_id: int | None
) -> WorkspaceChoiceSchema:
    workspace = membership.workspace
    return WorkspaceChoiceSchema(
        id=workspace.pk,
        name=workspace.name,
        slug=workspace.slug,
        role=membership.role,
        current=workspace.pk == current_id,
    )


@router.get("/workspaces", response=list[WorkspaceChoiceSchema])
def workspaces(request: HttpRequest) -> list[WorkspaceChoiceSchema]:
    """Every workspace the person belongs to, the first one joined first."""
    current = current_workspace(request)
    current_id = current.pk if current else None
    return [serialize_choice(item, current_id) for item in user_memberships(request)]


@router.post("/workspaces/current", response=WorkspaceChoiceSchema)
def choose_workspace(
    request: HttpRequest, payload: WorkspaceSelectSchema
) -> WorkspaceChoiceSchema:
    """Work in another workspace for the rest of this session."""
    workspace = select_workspace(request, payload.workspace_id)
    if workspace is None:
        raise HttpError(404, "Not found")
    membership = user_memberships(request).get(workspace=workspace)
    return serialize_choice(membership, workspace.pk)


@router.get("/invites/pending", response=PendingInviteSchema | None)
def show_pending_invite(request: HttpRequest) -> PendingInviteSchema | None:
    """The invite this browser opened, for the person to confirm or dismiss."""
    invite = pending_invite(request)
    if invite is None:
        return None
    if invite.workspace.memberships.filter(user_id=request.user.pk).exists():
        # Nothing to confirm: they already belong.
        forget_invite(request)
        return None
    inviter = invite.created_by
    return PendingInviteSchema(
        workspace_name=invite.workspace.name,
        role=invite.role,
        invited_by=(inviter.email or inviter.get_username()) if inviter else None,
    )


@router.post("/invites/pending/accept", response=WorkspaceChoiceSchema)
def accept_pending_invite(request: HttpRequest) -> WorkspaceChoiceSchema:
    """Join the invite's workspace and switch to it."""
    try:
        membership = join_pending_invite(request)
    except InviteError as error:
        raise HttpError(409, str(error)) from error
    select_workspace(request, membership.workspace_id)
    return serialize_choice(membership, membership.workspace_id)


@router.post("/invites/pending/dismiss", response={204: None})
def dismiss_pending_invite(request: HttpRequest) -> Status[None]:
    forget_invite(request)
    return Status(204, None)
