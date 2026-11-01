from datetime import datetime
from typing import Annotated, Any, Literal

from ninja import Schema
from pydantic import Field

from secondshift.connections.schemas import ConnectionSchema
from secondshift.runs.schemas import RunSchema


class AccessSchema(Schema):
    ok: bool
    scope: str | None
    text: str


class NeedJobSchema(Schema):
    job_id: int
    job_name: str
    repository: str
    capability_id: int
    required: bool
    grant_status: str
    access: AccessSchema | None = None


class CapabilityNeedSchema(Schema):
    binding: str
    kind: str
    required: bool
    requested_tools: list[str]
    status: Literal["missing", "unavailable", "needs_approval", "ready"]
    detail: str
    connection: ConnectionSchema | None
    jobs: list[NeedJobSchema]


class FixedRuleSchema(Schema):
    type: Literal["fixed"]
    value: Any


class AllowedRuleSchema(Schema):
    type: Literal["allowed"]
    values: list[Any] = Field(min_length=1)


class MaximumRuleSchema(Schema):
    type: Literal["maximum"]
    value: int | float


class ForbiddenRuleSchema(Schema):
    type: Literal["forbidden"]


ToolRuleSchema = Annotated[
    FixedRuleSchema | AllowedRuleSchema | MaximumRuleSchema | ForbiddenRuleSchema,
    Field(discriminator="type"),
]
# Tool name, then top-level argument name, then the rule.
ToolRulesSchema = dict[str, dict[str, ToolRuleSchema]]


class ToolInputSchema(Schema):
    """A tool's top-level arguments, so the review screen can offer rules."""

    properties: dict[str, dict[str, Any]]
    required: list[str]


class CapabilitySchema(Schema):
    id: int
    binding: str
    kind: str
    requested_tools: list[str]
    required: bool
    grant_status: str
    connection_id: int | None
    connection_name: str | None
    # External tools only: what the job could reach through this binding.
    access: AccessSchema | None = None
    # Requested tools that may change data on the service.
    changing_tools: list[str] = Field(default_factory=list)
    tool_rules: ToolRulesSchema = Field(default_factory=dict)
    # External tools only: each requested tool's arguments on the connection.
    tool_inputs: dict[str, ToolInputSchema] = Field(default_factory=dict)


class LastRunSchema(Schema):
    id: int
    status: str
    # When it finished, or when it was created if it has not finished.
    at: datetime


class JobSchema(Schema):
    id: int
    name: str
    repository_id: int
    repository: str
    skill_path: str
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    requested_capabilities: int
    grant_status: str
    capabilities: list[CapabilitySchema]
    last_run: LastRunSchema | None = None


class JobEnabledSchema(Schema):
    enabled: bool


class GrantDecisionSchema(Schema):
    decision: Literal["approved", "denied"]
    connection_id: int | None = None
    allowed_tools: list[str] | None = None
    # Left out keeps the current rules. An empty object removes them all.
    tool_rules: ToolRulesSchema | None = None


class LimitSchema(Schema):
    key: str
    label: str
    help: str
    unit: str
    value: int
    override: int | None
    inherited: int
    minimum: int
    maximum: int


class RunLimitsUpdateSchema(Schema):
    """Fields left out stay as they are. Null uses the inherited value."""

    max_turns: int | None = None
    max_tool_calls: int | None = None
    token_budget: int | None = None
    timeout_seconds: int | None = None


class JobDetailSchema(JobSchema):
    key: str
    config_hash: str
    history: int
    repository_revision: str
    repository_enabled: bool
    # The skill file at the synced commit. Empty until the next sync.
    skill_text: str
    skill_truncated: bool
    # The skill on GitHub at the synced commit. Empty for local repositories.
    skill_url: str
    recent_runs: list[RunSchema]
    limits: list[LimitSchema]
    can_edit_limits: bool
