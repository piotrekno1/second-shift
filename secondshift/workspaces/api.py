from datetime import datetime
from typing import Literal

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router, Schema, Status
from ninja.errors import HttpError
from ninja.security import django_auth
from pydantic import Field

from secondshift.workspaces.access import (
    Action,
    current_workspace,
    require,
    select_workspace,
    user_memberships,
)
from secondshift.workspaces.invites import (
    InviteError,
    create_invite,
    forget_invite,
    invite_path,
    join_pending_invite,
    pending_invite,
    pending_invites,
    revoke_invite,
)
from secondshift.workspaces.models import WorkspaceInvite, WorkspaceMembership

router = Router(auth=django_auth, tags=["workspaces"])


class WorkspaceChoiceSchema(Schema):
    id: int
    name: str
    slug: str
    role: str
    current: bool


class WorkspaceSelectSchema(Schema):
    workspace_id: int


class InviteCreateSchema(Schema):
    role: Literal["owner", "admin", "member"] = "member"
    note: str = Field("", max_length=160)


class InviteSchema(Schema):
    id: int
    role: str
    note: str
    created_by: str | None
    created_at: datetime
    expires_at: datetime


class NewInviteSchema(InviteSchema):
    # Shown once, right after creating it. It cannot be looked up later.
    link: str


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


def person_label(user: object | None) -> str | None:
    if user is None:
        return None
    return getattr(user, "email", "") or user.get_username()  # type: ignore[attr-defined]


def serialize_invite(invite: WorkspaceInvite) -> InviteSchema:
    return InviteSchema(
        id=invite.pk,
        role=invite.role,
        note=invite.note,
        created_by=person_label(invite.created_by),
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


@router.get("/invites", response=list[InviteSchema])
def invites(request: HttpRequest) -> list[InviteSchema]:
    """Invites in the current workspace that can still be accepted."""
    workspace = require(request, Action.INVITE_PEOPLE)
    queryset = pending_invites(workspace).select_related("created_by")
    return [serialize_invite(invite) for invite in queryset]


@router.post("/invites", response=NewInviteSchema)
def invite_person(request: HttpRequest, payload: InviteCreateSchema) -> NewInviteSchema:
    workspace = require(request, Action.INVITE_PEOPLE)
    new = create_invite(workspace, payload.role, request.user, payload.note.strip())
    return NewInviteSchema(
        **serialize_invite(new.invite).model_dump(),
        link=request.build_absolute_uri(invite_path(new.token)),
    )


@router.delete("/invites/{invite_id}", response={204: None})
def cancel_invite(request: HttpRequest, invite_id: int) -> Status[None]:
    workspace = require(request, Action.INVITE_PEOPLE)
    revoke_invite(get_object_or_404(pending_invites(workspace), pk=invite_id))
    return Status(204, None)
