from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field

from secondshift.automations.models import (
    Run,
)
from secondshift.automations.run_queries import Period, RunSort
from secondshift.integrations.schemas import ConnectionSchema


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


class JobEnabledSchema(Schema):
    enabled: bool


class GrantDecisionSchema(Schema):
    decision: Literal["approved", "denied"]
    connection_id: int | None = None
    allowed_tools: list[str] | None = None


class RunFailureSchema(Schema):
    label: str
    reason: str
    # The run stopped at one of its limits, which an owner can change.
    limit: bool = False


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


class RunSchema(Schema):
    id: int
    job_id: int
    job: str
    repository_id: int
    repository: str
    status: str
    scheduled_for: datetime
    started_at: datetime | None = None
    finished_at: datetime | None
    summary: str
    plan_ready: bool
    commit_sha: str = ""
    model: str | None = None
    max_turns: int | None = None
    token_budget: int | None = None
    model_requests: int = 0
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    failure: RunFailureSchema | None = None
    has_input: bool = False
    has_output: bool = False
    model_tokens: int = 0
    model_cost_usd: float | None = None
    model_cost_partial: bool = False


class RunEventSchema(Schema):
    id: int
    level: str
    message: str
    created_at: datetime


RunFileName = Literal["input", "output"]


class RunFileSchema(Schema):
    name: RunFileName
    label: str
    filename: str
    size_bytes: int | None
    truncated: bool


class RunDetailSchema(RunSchema):
    job_key: str
    skill_path: str
    created_at: datetime
    exit_code: int | None
    model_provider: str | None
    output_excerpt: str
    output_truncated: bool
    output_size_bytes: int | None
    events: list[RunEventSchema]
    files: list[RunFileSchema]


class JobDetailSchema(JobSchema):
    key: str
    config_hash: str
    repository_revision: str
    repository_enabled: bool
    recent_runs: list[RunSchema]
    limits: list[LimitSchema]
    can_edit_limits: bool


class RunListQuery(Schema):
    q: str = Field("", max_length=200)
    status: list[Run.Status] = []
    job_id: int | None = None
    repository_id: int | None = None
    period: Period | None = None
    sort: RunSort = "newest"
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=100)


class RunPageSchema(Schema):
    items: list[RunSchema]
    count: int
    page: int
    page_size: int
    status_counts: dict[str, int]
