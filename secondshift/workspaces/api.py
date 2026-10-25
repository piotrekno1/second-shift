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
from secondshift.workspaces.services import (
    MembershipError,
    WorkspaceCreationError,
    WorkspaceCreationRefused,
    change_role,
    create_workspace_for,
    members_in_order,
    remove_member,
)

router = Router(auth=django_auth, tags=["workspaces"])


class WorkspaceChoiceSchema(Schema):
    id: int
    name: str
    slug: str
    role: str
    current: bool


class WorkspaceSelectSchema(Schema):
    workspace_id: int


class WorkspaceCreateSchema(Schema):
    name: str = Field(max_length=160)


RoleName = Literal["owner", "admin", "member"]


class MemberSchema(Schema):
    id: int
    email: str
    role: str
    joined_at: datetime
    you: bool


class RoleChangeSchema(Schema):
    role: RoleName


class InviteCreateSchema(Schema):
    role: RoleName = "member"
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


@router.post("/workspaces", response=WorkspaceChoiceSchema)
def new_workspace(
    request: HttpRequest, payload: WorkspaceCreateSchema
) -> WorkspaceChoiceSchema:
    """Create a workspace owned by the person and switch to it."""
    try:
        workspace = create_workspace_for(request.user, payload.name)
    except WorkspaceCreationRefused as error:
        raise HttpError(403, str(error)) from error
    except WorkspaceCreationError as error:
        raise HttpError(400, str(error)) from error
    select_workspace(request, workspace.pk)
    membership = user_memberships(request).get(workspace=workspace)
    return serialize_choice(membership, workspace.pk)


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


def serialize_member(
    request: HttpRequest, membership: WorkspaceMembership
) -> MemberSchema:
    return MemberSchema(
        id=membership.pk,
        email=person_label(membership.user) or "",
        role=membership.role,
        joined_at=membership.created_at,
        you=membership.user_id == request.user.pk,
    )


def membership_for_request(
    request: HttpRequest, membership_id: int
) -> WorkspaceMembership:
    workspace = require(request, Action.MANAGE_MEMBERS)
    return get_object_or_404(
        WorkspaceMembership.objects.select_related("user"),
        pk=membership_id,
        workspace=workspace,
    )


@router.get("/members", response=list[MemberSchema])
def members(request: HttpRequest) -> list[MemberSchema]:
    """Everyone in the current workspace. Any member can see who belongs."""
    workspace = current_workspace(request)
    if workspace is None:
        return []
    return [serialize_member(request, item) for item in members_in_order(workspace)]


@router.post("/members/{membership_id}/role", response=MemberSchema)
def set_member_role(
    request: HttpRequest, membership_id: int, payload: RoleChangeSchema
) -> MemberSchema:
    membership = membership_for_request(request, membership_id)
    try:
        change_role(membership, payload.role)
    except MembershipError as error:
        raise HttpError(409, str(error)) from error
    return serialize_member(request, membership)


@router.delete("/members/{membership_id}", response={204: None})
def remove_person(request: HttpRequest, membership_id: int) -> Status[None]:
    membership = membership_for_request(request, membership_id)
    try:
        remove_member(membership)
    except MembershipError as error:
        raise HttpError(409, str(error)) from error
    return Status(204, None)
