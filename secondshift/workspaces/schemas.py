from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field

from secondshift.automations.schemas import (
    LimitSchema,
)


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
