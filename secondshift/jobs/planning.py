import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from django.conf import settings

from secondshift.config.constants import (
    COMMIT_SHA_PATTERN,
)
from secondshift.connections.dao import connection_for_binding, connections_by_binding
from secondshift.connections.execution import workspace_execution, workspace_model
from secondshift.connections.external import EXTERNAL_KINDS
from secondshift.connections.model_providers import provider_family
from secondshift.connections.models import Connection
from secondshift.jobs.automatic_grants import approves_automatically
from secondshift.jobs.capabilities import RequestedCapability, manifest_capabilities
from secondshift.jobs.dao import job_requests_with_grants
from secondshift.jobs.limits import effective_limits
from secondshift.jobs.manifest import JobManifest, TaskManifest, manifest_hash
from secondshift.jobs.models import Job, JobGrant
from secondshift.jobs.plan import (
    AgentPlan,
    CapabilityPlan,
    ExecutionChoice,
    ExecutionPlan,
    IssuesPlan,
    JobPlan,
    Missing,
    ModelPlan,
    PlanLimits,
    PlanReason,
    PullRequestPlan,
    RepositoryPlan,
    RunnerPlan,
)
from secondshift.jobs.tool_policy import plan_tools
from secondshift.jobs.tool_rules import ToolRules, broken_rules, kept_rules
from secondshift.repositories.github import repository_ref
from secondshift.repositories.models import Repository
from secondshift.workspaces.limits import RunLimit, workspace_limits
from secondshift.workspaces.models import Workspace


def model_plan(workspace: Workspace) -> ModelPlan | None:
    """The workspace's default model provider, frozen into the run's plan."""
    connection = workspace_model(workspace)
    if connection is None:
        return None
    return ModelPlan(
        connection_id=connection.pk,
        connection_name=connection.display_name,
        provider=connection.provider,
        family=provider_family(connection),
        default_model=connection.default_model,
        allowed_models=tuple(connection.allowed_models),
    )


@dataclass(frozen=True)
class CapabilityApproval:
    """Server-resolved policy. Never accepted from repository or client input."""

    status: str
    connection: Connection | None
    allowed_tools: tuple[str, ...]
    tool_rules: ToolRules = field(default_factory=dict)


@dataclass(frozen=True)
class PlannedCapability:
    request: RequestedCapability
    approval: CapabilityApproval | None = None


@dataclass(frozen=True)
class ExecutionDefinition:
    key: str
    skill_path: str
    config_hash: str


GRANT_REASONS = {
    JobGrant.Status.PENDING: PlanReason.GRANT_PENDING,
    JobGrant.Status.DENIED: PlanReason.GRANT_DENIED,
}
CONNECTION_REASONS = {
    Connection.Status.EXPIRED: PlanReason.CONNECTION_EXPIRED,
    Connection.Status.REVOKED: PlanReason.CONNECTION_REVOKED,
}
WAITING_REASONS = {
    PlanReason.NO_GRANT,
    PlanReason.GRANT_PENDING,
    PlanReason.TOOLS_NOT_GRANTED,
}


def blocking_reasons(
    workspace: Workspace,
    request: RequestedCapability,
    grant: CapabilityApproval | None,
    connection: Connection | None,
) -> list[str]:
    """Why a grant cannot be used for this request. Empty means it can."""
    requested = set(request.requested_tools)
    reasons: list[str] = []
    if not grant:
        reasons.append(PlanReason.NO_GRANT)
    elif grant.status != JobGrant.Status.APPROVED:
        reasons.append(GRANT_REASONS[grant.status])
    if connection:
        reasons.extend(connection_reasons(workspace, request, connection))
    if grant and grant.status == JobGrant.Status.APPROVED and connection is None:
        reasons.append(PlanReason.CONNECTION_MISSING)
    if grant and requested - set(grant.allowed_tools):
        reasons.append(PlanReason.TOOLS_NOT_GRANTED)
    if connection and requested - set(connection.available_tools):
        reasons.append(PlanReason.TOOLS_UNAVAILABLE)
    return reasons


def connection_reasons(
    workspace: Workspace, request: RequestedCapability, connection: Connection
) -> list[str]:
    reasons = []
    if connection.current_status != Connection.Status.CONNECTED:
        reasons.append(CONNECTION_REASONS[connection.current_status])
    if connection.workspace_id != workspace.pk:
        reasons.append(PlanReason.CONNECTION_WORKSPACE_MISMATCH)
    if connection.kind != request.kind:
        reasons.append(PlanReason.CONNECTION_KIND_MISMATCH)
    if connection.binding_key != request.binding:
        reasons.append(PlanReason.CONNECTION_BINDING_MISMATCH)
    return reasons


def rules_broken(grant: CapabilityApproval, connection: Connection) -> bool:
    """The connection's schemas changed under the owner's rules. Fail closed."""
    definitions = connection.tool_definitions
    return bool(grant.tool_rules and broken_rules(grant.tool_rules, definitions))


def missing_reason(
    workspace: Workspace,
    request: RequestedCapability,
    grant: CapabilityApproval | None,
    reasons: list[str],
) -> Missing:
    if grant and grant.status == JobGrant.Status.DENIED:
        return Missing.DENIED
    connection = (grant.connection if grant else None) or connection_for_binding(
        workspace.pk, request.binding
    )
    if connection is None or connection.status != Connection.Status.CONNECTED:
        return Missing.NOT_CONNECTED
    if WAITING_REASONS & set(reasons):
        return Missing.WAITING_FOR_APPROVAL
    return Missing.UNAVAILABLE


def capability_plan(
    workspace: Workspace, repository: Repository, capability: PlannedCapability
) -> CapabilityPlan:
    """One requested capability, with the tools the run may use."""
    request = capability.request
    grant = capability.approval
    connection = grant.connection if grant else None
    requested = set(request.requested_tools)
    allowed = set(grant.allowed_tools) if grant else set()
    available = set(connection.available_tools) if connection else set()
    effective = requested & allowed & available
    reasons = blocking_reasons(workspace, request, grant, connection)
    if grant and connection and rules_broken(grant, connection):
        reasons.append(PlanReason.TOOL_RULES_BROKEN)

    adapter = EXTERNAL_KINDS.get(request.kind)
    access = (
        adapter.access(connection, repository_ref(repository))
        if adapter and connection and connection.kind == request.kind
        else None
    )
    if access and not access.ok:
        reasons.append(access.reason)

    authorized = not reasons
    frozen = bool(adapter and connection and access and access.scope and authorized)
    return CapabilityPlan(
        binding=request.binding,
        kind=request.kind,
        required=request.required,
        requested_tools=tuple(sorted(requested)),
        connection_id=connection.pk if connection else None,
        connection_name=connection.display_name if connection else None,
        effective_tools=tuple(sorted(effective)) if authorized else (),
        authorized=authorized,
        reasons=tuple(reasons),
        missing=(
            None if authorized else missing_reason(workspace, request, grant, reasons)
        ),
        external=(
            adapter.freeze(connection, access.scope, effective) if frozen else None
        ),
        tool_rules=kept_rules(grant.tool_rules, effective) if frozen else {},
    )


def compile_execution_plan(
    workspace: Workspace,
    repository: Repository,
    definition: ExecutionDefinition,
    resolved_capabilities: Sequence[PlannedCapability],
    limits: dict[str, int],
    commit_sha: str,
    pull_request: PullRequestPlan | None = None,
    issues: IssuesPlan | None = None,
    subagents: int = 0,
) -> ExecutionPlan:
    """Compile server-resolved policy into the plan a run keeps."""
    if repository.workspace_id != workspace.pk:
        raise ValueError("Repository and execution policy must share a workspace.")
    capabilities = tuple(
        capability_plan(workspace, repository, item) for item in resolved_capabilities
    )
    selection = workspace_execution(workspace)
    connection = selection.connection
    ready = selection.ready and all(
        item.authorized for item in capabilities if item.required
    )
    agent = AgentPlan(
        limits=PlanLimits(
            max_turns=limits[RunLimit.MAX_TURNS],
            max_tool_calls=limits[RunLimit.MAX_TOOL_CALLS],
            max_tool_result_chars=settings.AGENT_MAX_TOOL_RESULT_CHARS,
            token_budget=limits[RunLimit.TOKEN_BUDGET],
            timeout_seconds=limits[RunLimit.TIMEOUT_SECONDS],
        ),
        tools=tuple(tool.name for tool in plan_tools(ready, capabilities)),
        # Backends that cannot start sub-agents get none. Lowering the server
        # maximum applies before the manifest is synced again.
        subagents=(
            min(subagents, settings.RUN_SUBAGENTS_MAX)
            if selection.backend.starts_subagents
            else 0
        ),
    )
    return ExecutionPlan(
        execution=ExecutionChoice(
            backend=selection.backend,
            label=selection.label,
            ready=selection.ready,
            problem=selection.problem,
            connection_id=connection.pk if connection else None,
            credential_key=str(connection.credential_key) if connection else None,
            owner_id=connection.owner_id if connection else None,
        ),
        ready=ready,
        agent=agent,
        job=JobPlan(
            key=definition.key,
            skill_path=definition.skill_path,
            config_hash=definition.config_hash,
        ),
        repository=RepositoryPlan(
            full_name=repository.full_name, config_sha=commit_sha
        ),
        runner=RunnerPlan(
            image=settings.RUNNER_IMAGE,
            network_policy="none",
            collect_changes=pull_request is not None,
        ),
        pull_request=pull_request,
        issues=issues,
        model=model_plan(workspace) if selection.backend.uses_model_api else None,
        capabilities=capabilities,
    )


def build_execution_plan(job: Job) -> ExecutionPlan:
    """The plan for a run of this saved job, with its grants."""
    capabilities = []
    for request in job_requests_with_grants(job):
        grant = getattr(request, "grant", None)
        capabilities.append(
            PlannedCapability(
                RequestedCapability(
                    request.binding,
                    request.kind,
                    tuple(request.requested_tools),
                    request.required,
                ),
                CapabilityApproval(
                    grant.status,
                    grant.connection,
                    tuple(grant.allowed_tools),
                    grant.tool_rules,
                )
                if grant is not None
                else None,
            )
        )
    return compile_execution_plan(
        job.workspace,
        job.repository,
        ExecutionDefinition(job.key, job.skill_path, job.config_hash),
        capabilities,
        effective_limits(job),
        job.repository.config_sha,
        # Jobs run on the followed branch, so their drafts target it.
        PullRequestPlan(
            base=job.repository.default_branch, run_ci=job.repository.pull_request_ci
        )
        if job.open_pull_request
        else None,
        issues=(
            IssuesPlan(max=issues.max, labels=tuple(issues.labels))
            if (issues := job.issue_settings)
            else None
        ),
        subagents=job.subagents,
    )


def build_skill_execution_plan(
    repository: Repository,
    definition: JobManifest | TaskManifest,
    *,
    key: str,
    commit_sha: str,
    pull_request: PullRequestPlan | None = None,
) -> ExecutionPlan:
    """Plan an ad hoc repository skill without saving jobs, requests or grants.

    The caller resolves the repository revision to a commit before planning.
    Logical capabilities use only connections that allow automatic approval;
    grants for other jobs are never inherited. Limits and execution selection
    come from the workspace, and sandbox policy comes from server settings.
    This does not check out skill files or submit an execution.
    """
    if not re.fullmatch(COMMIT_SHA_PATTERN, commit_sha):
        raise ValueError("A full commit SHA is required before planning a skill.")
    requests = manifest_capabilities(definition)
    connections = connections_by_binding(
        repository.workspace_id, [request.binding for request in requests]
    )
    capabilities = []
    for request in requests:
        connection = connections.get(request.binding)
        approval = None
        if connection is not None and approves_automatically(
            request, connection, repository
        ):
            approval = CapabilityApproval(
                JobGrant.Status.APPROVED, connection, request.requested_tools
            )
        capabilities.append(PlannedCapability(request, approval))
    return compile_execution_plan(
        repository.workspace,
        repository,
        ExecutionDefinition(key, definition.skill or "", manifest_hash(definition)),
        capabilities,
        workspace_limits(repository.workspace),
        commit_sha,
        pull_request,
        subagents=definition.subagents,
    )
