from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field


class ConnectionCheckSchema(Schema):
    ok: bool
    message: str
    checked_at: datetime


class ConnectionUsageSchema(Schema):
    jobs: int
    runs_7d: int
    spend_7d_usd: float
    spend_partial: bool
    last_used_at: datetime | None


class OfferedToolSchema(Schema):
    name: str
    description: str
    read_only: bool


class ConnectionSchema(Schema):
    id: int
    provider: str
    kind: str
    binding_key: str
    display_name: str
    status: str
    expires_at: datetime | None
    available_tools: list[str]
    target: str
    allowed_models: list[str]
    default_model: str
    is_default: bool
    available_models: list[str]
    last_check: ConnectionCheckSchema | None = None
    usage: ConnectionUsageSchema | None = None
    # MCP only: the organization or project an owner chose. Never secret.
    settings: dict[str, str] = Field(default_factory=dict)
    # MCP only: "oauth" for a browser sign-in, "token" for a pasted token.
    auth: str = ""
    scopes: list[str] = Field(default_factory=list)
    # Custom MCP servers: every tool the server offers, for the owner to choose.
    offered_tools: list[OfferedToolSchema] = Field(default_factory=list)


class WebhookConnectionCreateSchema(Schema):
    binding_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    display_name: str = Field(default="", max_length=200)
    url: str = Field(min_length=1, max_length=2000)


class WebhookConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)


class MCPConnectionCreateSchema(Schema):
    provider: str = Field(min_length=1, max_length=40)
    binding_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    display_name: str = Field(default="", max_length=200)
    token: str = Field(min_length=1, max_length=2000)
    settings: dict[str, str] = Field(default_factory=dict, max_length=10)


class MCPConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    token: str | None = Field(default=None, max_length=2000)
    settings: dict[str, str] | None = Field(default=None, max_length=10)
    tools: list[str] | None = Field(default=None, max_length=200)


class APISpecSchema(Schema):
    spec_url: str = Field(default="", max_length=2000)
    spec_text: str = Field(default="", max_length=5 * 1024 * 1024)


class APISpecSummarySchema(Schema):
    title: str
    servers: list[str]
    operations: int
    read_only: int


class APIConnectionCreateSchema(APISpecSchema):
    binding_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    display_name: str = Field(default="", max_length=200)
    base_url: str = Field(min_length=1, max_length=200)
    auth: str = Field(default="bearer", max_length=20)
    header: str = Field(default="", max_length=64)
    token: str = Field(min_length=1, max_length=2000)


class APIConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    token: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=200)
    operations: list[str] | None = Field(default=None, max_length=500)


class MCPInspectSchema(Schema):
    provider: str = Field(min_length=1, max_length=40)
    settings: dict[str, str] = Field(default_factory=dict, max_length=10)


class MCPInspectResultSchema(Schema):
    oauth: bool
    scopes: list[str]


class MCPSignInSchema(Schema):
    provider: str = Field(min_length=1, max_length=40)
    binding_key: str = Field(
        default="", max_length=120, pattern=r"^([a-z0-9]+(?:-[a-z0-9]+)*)?$"
    )
    display_name: str = Field(default="", max_length=200)
    settings: dict[str, str] = Field(default_factory=dict, max_length=10)
    scopes: list[str] = Field(default_factory=list, max_length=20)
    # Reconnect this connection instead of creating one.
    connection_id: int | None = None


class MCPSignInStartSchema(Schema):
    authorize_url: str


class MCPSettingSchema(Schema):
    key: str
    label: str
    pattern: str
    required: bool
    help: str
    placeholder: str


class MCPProviderSchema(Schema):
    key: str
    label: str
    description: str
    default_binding: str
    token_label: str
    token_hint: str
    setup_url: str
    setup_help: str
    settings: list[MCPSettingSchema]
    tools: list[str]
    auth_methods: list[str]
    owner_selects_tools: bool


class ModelConnectionCreateSchema(Schema):
    provider: Literal["anthropic", "openai", "openrouter", "openai_compatible"]
    display_name: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=1000)
    base_url: str = Field(default="", max_length=2000)
    allowed_models: list[str] = Field(default_factory=list, max_length=100)
    default_model: str = Field(default="", max_length=200)
    is_default: bool | None = None


class ModelConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    base_url: str | None = Field(default=None, max_length=2000)
    allowed_models: list[str] | None = Field(default=None, max_length=100)
    default_model: str | None = Field(default=None, max_length=200)
    is_default: bool | None = None


class ConnectionTestSchema(Schema):
    ok: bool
    status_code: int | None
    message: str
    models: list[str] = Field(default_factory=list)


class SecretSchema(Schema):
    name: str
    description: str
    last_used_at: datetime | None
