from datetime import datetime
from typing import Annotated, Any, Literal

from ninja import Schema
from pydantic import Field, SecretStr, WithJsonSchema

from secondshift.connections.dao import has_pending_cleanup
from secondshift.connections.execution import recent_login_runs
from secondshift.connections.mcp.base import SOURCE_AUTH
from secondshift.connections.models import AgentConnection, Connection
from secondshift.runner_protocol.agents import AgentBackend
from secondshift.workspaces.access import Action, allows


class FeatureCheckSchema(Schema):
    feature: str
    label: str
    # None when it cannot be checked without using it.
    ok: bool | None
    message: str


class ConnectionCheckSchema(Schema):
    ok: bool
    message: str
    checked_at: datetime
    # Source connections: what each feature can do, and where to fix it.
    features: list[FeatureCheckSchema] = Field(default_factory=list)
    fix_url: str = ""


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


# Ninja shares component names across endpoints, including other Kind and Status enums.
ConnectionKind = Annotated[
    Connection.Kind, WithJsonSchema({"type": "string", "enum": Connection.Kind.values})
]
ConnectionStatus = Annotated[
    Connection.Status,
    WithJsonSchema({"type": "string", "enum": Connection.Status.values}),
]


class ConnectionSchema(Schema):
    id: int
    provider: Connection.Provider
    kind: ConnectionKind
    binding_key: str
    display_name: str
    status: ConnectionStatus
    expires_at: datetime | None
    available_tools: list[str]
    target: str
    allowed_models: list[str]
    default_model: str
    is_default: bool
    available_models: list[str]
    last_check: ConnectionCheckSchema | None
    usage: ConnectionUsageSchema | None
    # MCP only: the organization or project an owner chose. Never secret.
    settings: dict[str, str]
    # MCP only: "oauth" for a browser sign-in, "token" for a pasted token.
    auth: Literal["oauth", "token", "app", SOURCE_AUTH, ""]
    scopes: list[str]
    requested_scopes: list[str]
    # Custom MCP servers: every tool the server offers, for the owner to choose.
    offered_tools: list[OfferedToolSchema]
    # Capability connections: whether each job's request waits for an owner.
    require_approval: bool
    # GitHub sources: whether pull requests, issues and commits name SecondShift.
    attribution: bool


class AttributionSchema(Schema):
    attribution: bool


class WebhookConnectionCreateSchema(Schema):
    binding_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    display_name: str = Field(default="", max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    require_approval: bool = False


class WebhookConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    require_approval: bool | None = None


class GitHubTokenConnectSchema(Schema):
    token: str = Field(min_length=1, max_length=2000)
    # Replace the token of this connection instead of adding one.
    connection_id: int | None = None


class MCPConnectionCreateSchema(Schema):
    provider: str = Field(min_length=1, max_length=40)
    binding_key: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    display_name: str = Field(default="", max_length=200)
    # Empty when auth is "source": the connection borrows repository sources.
    token: str = Field(default="", max_length=2000)
    auth: str = Field(default="token", pattern=r"^(token|source)$")
    settings: dict[str, str] = Field(default_factory=dict, max_length=10)
    require_approval: bool = False


class MCPConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    token: str | None = Field(default=None, max_length=2000)
    settings: dict[str, str] | None = Field(default=None, max_length=10)
    tools: list[str] | None = Field(default=None, max_length=200)
    require_approval: bool | None = None


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
    require_approval: bool = False


class APIConnectionUpdateSchema(Schema):
    display_name: str | None = Field(default=None, max_length=200)
    token: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=200)
    operations: list[str] | None = Field(default=None, max_length=500)
    require_approval: bool | None = None


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
    # New connections only. A reconnect keeps the connection's setting.
    require_approval: bool = False
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
    models: list[str] = []


class WebhookSampleSchema(Schema):
    # A run report as a webhook receives it, built from made-up records.
    payload: dict[str, Any]
    # How many characters of the report the summary field can hold.
    summary_max_chars: int


class AgentCheckSchema(Schema):
    ok: bool
    reason: str
    message: str
    checked_at: str


class AgentConnectionSchema(Schema):
    id: int
    backend: Literal[AgentBackend.CLAUDE_CODE, AgentBackend.CODEX]
    display_name: str
    connected: bool
    owner_id: int
    updated_at: datetime
    max_runs_per_day: int | None
    # Runs in the last 24 hours, the window the cap counts over.
    recent_runs: int = 0
    can_manage: bool = False
    last_check: AgentCheckSchema | None
    # The runner still holds a cached copy of an earlier login.
    runner_cleanup_pending: bool = False

    @staticmethod
    def resolve_recent_runs(obj: AgentConnection) -> int:
        # A list counts every login at once and attaches the counts.
        counted = getattr(obj, "recent_runs", None)
        if counted is None:
            counted = recent_login_runs(obj.workspace_id)[obj.pk]
        return counted

    @staticmethod
    def resolve_runner_cleanup_pending(obj: AgentConnection) -> bool:
        return has_pending_cleanup(obj)

    @staticmethod
    def resolve_can_manage(obj: AgentConnection, context: dict[str, Any]) -> bool:
        request = context["request"]
        return obj.owner_id == request.user.pk and allows(
            request, Action.EDIT_CONNECTIONS
        )


class AgentRunLimitInput(Schema):
    # Empty removes the cap.
    max_runs_per_day: int | None = Field(default=None, ge=1, le=1000)


class AgentConnectionInput(Schema):
    backend: str
    display_name: str = Field(default="", max_length=200)
    auth: SecretStr = Field(max_length=32000)


class ExecutionChoiceSchema(Schema):
    key: str
    label: str
    disabled: bool = False
    # Why this choice cannot run jobs, or empty when it can.
    problem: str = ""


class ExecutionStatusSchema(Schema):
    label: str
    backend: str
    problem: str
    # False until an owner picks a model provider or a login.
    chosen: bool
    # Nothing to choose yet: no model provider and no login saved.
    needs_setup: bool
    fix_connection_id: int | None


class ExecutionConfigurationSchema(Schema):
    enabled: bool
    choices: list[ExecutionChoiceSchema]
    selected: str
    label: str
    backend: str
    ready: bool
    problem: str


class ExecutionPreferenceSchema(Schema):
    key: str = Field(max_length=100)
