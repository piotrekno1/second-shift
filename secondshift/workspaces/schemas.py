from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field

from secondshift.jobs.schemas import LimitSchema


class SettingValueSchema(Schema):
    label: str
    value: str
    env: str
    help: str


class SettingGroupSchema(Schema):
    title: str
    items: list[SettingValueSchema]


class WorkspaceSettingsSchema(Schema):
    workspace_name: str | None
    workspace_slug: str | None
    environment: str
    sandbox: str
    groups: list[SettingGroupSchema]
    # Run limits for the whole workspace. Each job can choose its own.
    limits: list[LimitSchema]
    can_edit_limits: bool


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


class MemberSchema(Schema):
    id: int
    email: str
    role: str
    joined_at: datetime
    you: bool


RoleName = Literal["owner", "admin", "member"]


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


ScopeName = Literal["read", "run"]


class ApiTokenSchema(Schema):
    id: int
    name: str
    scope: str
    workspace_id: int
    workspace_name: str
    workspace_slug: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None


class TokenWorkspaceSchema(Schema):
    """A workspace the person can create a token for."""

    id: int
    name: str
    slug: str
    current: bool
    can_create_run_tokens: bool


class ApiTokenListSchema(Schema):
    items: list[ApiTokenSchema]
    workspaces: list[TokenWorkspaceSchema]
    # The longest expiry, in days, a new token can have.
    max_days: int


class ApiTokenCreateSchema(Schema):
    workspace_id: int
    name: str = Field(min_length=1, max_length=160)
    scope: ScopeName = "read"
    expires_in_days: int


class NewApiTokenSchema(ApiTokenSchema):
    # Shown once, right after creating it. It cannot be looked up later.
    token: str
