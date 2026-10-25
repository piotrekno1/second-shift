from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.workspaces.access import (
    current_workspace,
    select_workspace,
    user_memberships,
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
