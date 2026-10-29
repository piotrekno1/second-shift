from django.http import HttpRequest

from secondshift.core.workspace_settings import (
    SettingValue,
    environment_label,
    sandbox_label,
    setting_groups,
)
from secondshift.jobs.api_limits import serialize_limits
from secondshift.jobs.limits import limit_views
from secondshift.workspaces.access import (
    Action,
    allows,
    is_operator,
)
from secondshift.workspaces.models import (
    ApiToken,
    Workspace,
    WorkspaceInvite,
    WorkspaceMembership,
)
from secondshift.workspaces.schemas import (
    ApiTokenSchema,
    InviteSchema,
    MemberSchema,
    SettingGroupSchema,
    SettingValueSchema,
    TokenWorkspaceSchema,
    WorkspaceChoiceSchema,
    WorkspaceSettingsSchema,
)
from secondshift.workspaces.tokens import scope_allowed


def serialize_setting(request: HttpRequest, item: SettingValue) -> SettingValueSchema:
    # Only people who run the server can set the variable, so only they see it.
    env = item.env if is_operator(request) else ""
    return SettingValueSchema(**{**vars(item), "env": env})


def serialize_settings(
    request: HttpRequest, workspace: Workspace | None
) -> WorkspaceSettingsSchema:
    return WorkspaceSettingsSchema(
        workspace_name=workspace.name if workspace else None,
        workspace_slug=workspace.slug if workspace else None,
        environment=environment_label(),
        sandbox=sandbox_label(),
        groups=[
            SettingGroupSchema(
                title=group.title,
                items=[serialize_setting(request, item) for item in group.items],
            )
            for group in setting_groups()
        ],
        limits=serialize_limits(limit_views(workspace)) if workspace else [],
        can_edit_limits=allows(request, Action.CHANGE_RUN_LIMITS),
    )


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


def person_label(user: object | None) -> str | None:
    if user is None:
        return None
    return getattr(user, "email", "") or user.get_username()


def serialize_invite(invite: WorkspaceInvite) -> InviteSchema:
    return InviteSchema(
        id=invite.pk,
        role=invite.role,
        note=invite.note,
        created_by=person_label(invite.created_by),
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


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


def serialize_api_token(token: ApiToken) -> ApiTokenSchema:
    return ApiTokenSchema(
        id=token.pk,
        name=token.name,
        scope=token.scope,
        workspace_id=token.workspace_id,
        workspace_name=token.workspace.name,
        workspace_slug=token.workspace.slug,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
    )


def serialize_token_workspace(
    membership: WorkspaceMembership, current_id: int | None
) -> TokenWorkspaceSchema:
    workspace = membership.workspace
    return TokenWorkspaceSchema(
        id=workspace.pk,
        name=workspace.name,
        slug=workspace.slug,
        current=workspace.pk == current_id,
        can_create_run_tokens=scope_allowed(membership.role, ApiToken.Scope.RUN),
    )
