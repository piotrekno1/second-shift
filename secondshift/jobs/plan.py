"""The execution plan frozen into each run.

The planner builds it once from server-owned policy. Runs, brokers and pages
read it through these types. It never holds credentials.
"""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from secondshift.config.constants import SANDBOX_SKILL_DIR, AgentBackend
from secondshift.workspaces.limits import RunLimit


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Missing(StrEnum):
    """Why a run does not have a binding, in words safe to show the model."""

    NOT_CONNECTED = "not connected"
    WAITING_FOR_APPROVAL = "waiting for approval"
    DENIED = "denied"
    UNAVAILABLE = "not available"


class PlanReason(StrEnum):
    """Why a capability is not authorized. Connections add AccessReason codes."""

    NO_GRANT = "no_grant"
    GRANT_PENDING = "grant_pending"
    GRANT_DENIED = "grant_denied"
    CONNECTION_MISSING = "connection_missing"
    CONNECTION_EXPIRED = "connection_expired"
    CONNECTION_REVOKED = "connection_revoked"
    CONNECTION_WORKSPACE_MISMATCH = "connection_workspace_mismatch"
    CONNECTION_KIND_MISMATCH = "connection_kind_mismatch"
    CONNECTION_BINDING_MISMATCH = "connection_binding_mismatch"
    TOOLS_NOT_GRANTED = "tools_not_granted"
    TOOLS_UNAVAILABLE = "tools_unavailable"
    TOOL_RULES_BROKEN = "tool_rules_broken"


class ExecutionChoice(Frozen):
    """The agent backend the workspace chose, and the login a CLI agent uses."""

    backend: AgentBackend
    label: str
    ready: bool
    problem: str
    connection_id: int | None = None
    credential_key: str | None = None
    owner_id: int | None = None


class PlanLimits(Frozen):
    max_turns: int
    max_tool_calls: int
    max_tool_result_chars: int
    token_budget: int
    timeout_seconds: int

    def value(self, limit: RunLimit) -> int:
        return int(getattr(self, limit))


class AgentPlan(Frozen):
    limits: PlanLimits
    # The only tool names the agent may call.
    tools: tuple[str, ...]
    subagents: int = 0


class SkillSourcePlan(Frozen):
    """A skill from another repository, pinned to the commit the run gets."""

    repository: str
    commit_sha: str


class JobPlan(Frozen):
    key: str
    # A path in the run's repository, or in skill_source's when it is set.
    skill_path: str
    config_hash: str
    skill_source: SkillSourcePlan | None = None

    @property
    def sandbox_skill_path(self) -> str:
        """Where the agent reads the skill. Empty for written instructions."""
        if self.skill_source is not None:
            return f"{SANDBOX_SKILL_DIR}/{PurePosixPath(self.skill_path).name}"
        return self.skill_path


class RepositoryPlan(Frozen):
    full_name: str
    config_sha: str


class RunnerPlan(Frozen):
    image: str
    network_policy: Literal["none"]
    # Only runs that publish the agent's changes need them recorded.
    collect_changes: bool


class PullRequestPlan(Frozen):
    """The draft pull request's base branch and CI choice."""

    base: str
    run_ci: bool


class IssuesPlan(Frozen):
    """How many issues the agent may propose, and the labels each gets."""

    max: int
    labels: tuple[str, ...]


class ModelPlan(Frozen):
    connection_id: int
    connection_name: str
    provider: str
    family: str
    default_model: str
    allowed_models: tuple[str, ...]


class CapabilityPlan(Frozen):
    binding: str
    kind: str
    required: bool
    requested_tools: tuple[str, ...]
    connection_id: int | None
    connection_name: str | None
    effective_tools: tuple[str, ...]
    authorized: bool
    # PlanReason and AccessReason codes. Older runs may hold retired ones.
    reasons: tuple[str, ...]
    missing: Missing | None = None
    # The approved scope and tool definitions of an external tool.
    external: dict[str, Any] | None = None
    # Copied in so a rule changed mid-run does not affect the run.
    tool_rules: dict[str, dict[str, Any]] = {}


class ExecutionPlan(Frozen):
    execution: ExecutionChoice
    ready: bool
    agent: AgentPlan
    job: JobPlan
    repository: RepositoryPlan
    runner: RunnerPlan
    pull_request: PullRequestPlan | None
    issues: IssuesPlan | None
    model: ModelPlan | None
    capabilities: tuple[CapabilityPlan, ...]

    def limit(self, limit: RunLimit) -> int:
        return self.agent.limits.value(limit)

    def capability(self, kind: str, binding: str) -> CapabilityPlan:
        return next(
            item
            for item in self.capabilities
            if item.kind == kind and item.binding == binding
        )

    def stored(self) -> dict[str, Any]:
        """The JSON saved on the run."""
        return self.model_dump(mode="json")
