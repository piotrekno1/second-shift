"""Who may do what in a workspace.

Access comes only from WorkspaceMembership. Each request works in one
workspace: the first one the person joined. Every check names an Action, and
POLICY says which role it needs.
"""

from enum import StrEnum

from django.db.models import QuerySet
from django.http import HttpRequest

from secondshift.core.models import Workspace, WorkspaceMembership

Role = WorkspaceMembership.Role

# Higher roles can do everything lower roles can.
ROLE_RANK = {Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}
# The resolved membership is kept on the request under this name.
MEMBERSHIP_ATTRIBUTE = "secondshift_membership"


class Action(StrEnum):
    """Each value finishes the sentence "Only ... can"."""

    # Owners: credentials, approvals and the host's files.
    ADD_LOCAL_REPOSITORIES = "add local repositories"
    VIEW_LOCAL_PATHS = "see local repository paths"
    ADD_CONNECTIONS = "add connections"
    ADD_MCP_CONNECTIONS = "add MCP connections"
    ADD_API_CONNECTIONS = "add API connections"
    ADD_MODEL_PROVIDERS = "add model providers"
    EDIT_CONNECTIONS = "edit connections"
    EDIT_MODEL_PROVIDERS = "edit model providers"
    DISCONNECT_CONNECTIONS = "disconnect connections"
    DELETE_CONNECTIONS = "delete connections"
    TEST_CONNECTIONS = "test connections"
    REVIEW_CAPABILITIES = "review capabilities"
    # Admins: running the work that owners approved.
    RESYNC_REPOSITORIES = "resync repositories"
    CHANGE_REPOSITORY_STATE = "change repository state"
    REMOVE_REPOSITORIES = "remove repositories"
    ENABLE_JOBS = "enable jobs"
    RUN_JOBS = "run jobs"
    CHANGE_RUN_LIMITS = "change run limits"


ADMIN_ACTIONS = {
    Action.RESYNC_REPOSITORIES,
    Action.CHANGE_REPOSITORY_STATE,
    Action.REMOVE_REPOSITORIES,
    Action.ENABLE_JOBS,
    Action.RUN_JOBS,
    Action.CHANGE_RUN_LIMITS,
}
# The lowest role allowed to take each action. Members can only view.
POLICY = {
    action: Role.ADMIN if action in ADMIN_ACTIONS else Role.OWNER for action in Action
}


class AccessDenied(Exception):
    pass


def role_allows(role: str | None, action: Action) -> bool:
    if role is None:
        return False
    return ROLE_RANK[Role(role)] >= ROLE_RANK[POLICY[action]]


def denial_message(action: Action) -> str:
    if POLICY[action] == Role.OWNER:
        return f"Only workspace owners can {action}."
    return f"Only workspace owners and admins can {action}."


def user_memberships(request: HttpRequest) -> QuerySet[WorkspaceMembership]:
    """The signed-in person's memberships, the first one joined first."""
    return (
        WorkspaceMembership.objects.filter(user_id=request.user.pk)
        .select_related("workspace")
        .order_by("created_at", "pk")
    )


def current_membership(request: HttpRequest) -> WorkspaceMembership | None:
    if hasattr(request, MEMBERSHIP_ATTRIBUTE):
        return getattr(request, MEMBERSHIP_ATTRIBUTE)
    membership = None
    if request.user.is_authenticated:
        membership = user_memberships(request).first()
    setattr(request, MEMBERSHIP_ATTRIBUTE, membership)
    return membership


def current_workspace(request: HttpRequest) -> Workspace | None:
    membership = current_membership(request)
    return membership.workspace if membership else None


def current_role(request: HttpRequest) -> str | None:
    membership = current_membership(request)
    return membership.role if membership else None


def allows(request: HttpRequest, action: Action) -> bool:
    return role_allows(current_role(request), action)


def permitted_actions(request: HttpRequest) -> list[Action]:
    return [action for action in Action if allows(request, action)]


def require(request: HttpRequest, action: Action) -> Workspace:
    """The current workspace, when the person's role allows the action."""
    membership = current_membership(request)
    if membership is None or not role_allows(membership.role, action):
        raise AccessDenied(denial_message(action))
    return membership.workspace
