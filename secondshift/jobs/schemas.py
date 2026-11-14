from datetime import datetime
from typing import Annotated, Any, Literal

from ninja import Schema
from pydantic import ConfigDict, Field

from secondshift.config.constants import AgentBackend
from secondshift.connections.schemas import ConnectionSchema
from secondshift.runs.schemas import RunSchema
from secondshift.workspaces.schemas import LimitSchema


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
    execution_backend: str = AgentBackend.PYDANTIC
    execution_label: str = ""
    execution_problem: str = ""
    name: str
    repository_id: int
    repository: str
    skill_path: str
    # Another public GitHub repository the skill comes from. Empty when the
    # skill is in the job's own repository.
    skill_repository: str = ""
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    # When a sync stopped finding the job in the manifest. It cannot run then.
    removed_at: datetime | None = None
    requested_capabilities: int
    grant_status: str
    capabilities: list[CapabilitySchema]
    last_run: LastRunSchema | None = None
    # Trigger kinds the configuration names, such as "webhook".
    triggers: list[str] = []


class JobEnabledSchema(Schema):
    enabled: bool


class GrantDecisionSchema(Schema):
    decision: Literal["approved", "denied"]
    connection_id: int | None = None
    allowed_tools: list[str] | None = None
    # Left out keeps the current rules. An empty object removes them all.
    tool_rules: ToolRulesSchema | None = None


class OpenIssuesSchema(Schema):
    labels: list[str]
    max: int


class JobDetailSchema(JobSchema):
    key: str
    config_hash: str
    history: int
    subagents: int
    # The configuration asks for a draft pull request with each run's changes.
    open_pull_request: bool
    # A GitHub repository, so drafts could open. Local repositories cannot.
    can_open_pull_requests: bool
    # An owner allows pull requests for the repository, so drafts open.
    pull_requests_allowed: bool
    # The configuration asks to open the issues each run proposes. None if not.
    open_issues: OpenIssuesSchema | None
    # An owner allows issues for the repository, so they open.
    issues_allowed: bool
    repository_revision: str
    # The commit the skill is read from: the repository's, or the pinned
    # commit of the repository in skill_repository.
    skill_revision: str = ""
    repository_enabled: bool
    # The skill file at the synced commit. Empty until the next sync.
    skill_text: str
    skill_truncated: bool
    # The skill on GitHub at the synced commit. Empty for local repositories.
    skill_url: str
    recent_runs: list[RunSchema]
    limits: list[LimitSchema]
    can_edit_limits: bool


class SkillInspectionInput(Schema):
    model_config = ConfigDict(extra="forbid")

    # owner/name of a public GitHub repository.
    repository: str = Field(min_length=3, max_length=200)
    # The SKILL.md path in that repository.
    path: str = Field(min_length=1, max_length=500)
    # A branch, tag or full commit SHA. The default branch when left out.
    ref: str | None = Field(default=None, min_length=1, max_length=200)
    # The repository a task would run in, to check its connections' access.
    repository_id: int | None = Field(default=None, gt=0)


class SkillNeedSchema(Schema):
    binding: str
    server: str
    tools: list[str]
    origin: str
    status: str
    detail: str


class SkillInspectionSchema(Schema):
    repository: str
    path: str
    commit_sha: str
    # The skill folder's files, as `sha256:<hex>`.
    digest: str
    url: str
    name: str
    description: str
    file_count: int
    size_bytes: int
    # The SKILL.md text. It comes from someone else's repository, so treat it
    # as data and not as instructions.
    text: str
    text_truncated: bool
    needs: list[SkillNeedSchema]
