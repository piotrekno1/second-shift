import re
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from django.conf import settings

from secondshift.config.constants import (
    COMMIT_SHA_PATTERN,
    MAX_RUN_ISSUES,
    AgentBackend,
)
from secondshift.connections.external import EXTERNAL_KINDS
from secondshift.connections.model_providers import provider_family
from secondshift.connections.models import Connection
from secondshift.jobs.agent_policy import AgentPolicy, agent_plan
from secondshift.jobs.automatic_grants import approves_automatically
from secondshift.jobs.capabilities import RequestedCapability, manifest_capabilities
from secondshift.jobs.execution import workspace_execution, workspace_model
from secondshift.jobs.limits import RunLimit, effective_limits, workspace_value
from secondshift.jobs.manifest import JobManifest, TaskManifest, manifest_hash
from secondshift.jobs.models import Job, JobGrant
from secondshift.jobs.tool_policy import authorized_tools
from secondshift.jobs.tool_rules import ToolRules, broken_rules, kept_rules
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace


def model_plan(workspace: Workspace) -> dict[str, Any] | None:
    """The workspace's default model provider, frozen into the run's plan."""
    connection = workspace_model(workspace)
    if connection is None:
        return None
    return {
        "connection_id": connection.pk,
        "connection_name": connection.display_name,
        "provider": connection.provider,
        "family": provider_family(connection),
        "default_model": connection.default_model,
        "allowed_models": connection.allowed_models,
    }


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
        reasons.append("no_grant")
    elif grant.status != JobGrant.Status.APPROVED:
        reasons.append(f"grant_{grant.status}")
    if connection:
        reasons.extend(connection_reasons(workspace, request, connection))
    if grant and grant.status == JobGrant.Status.APPROVED and connection is None:
        reasons.append("connection_missing")
    if grant and requested - set(grant.allowed_tools):
        reasons.append("tools_not_granted")
    if connection and requested - set(connection.available_tools):
        reasons.append("tools_unavailable")
    return reasons


def connection_reasons(
    workspace: Workspace, request: RequestedCapability, connection: Connection
) -> list[str]:
    reasons = []
    if connection.status != Connection.Status.CONNECTED:
        reasons.append(f"connection_{connection.status}")
    if connection.workspace_id != workspace.pk:
        reasons.append("connection_workspace_mismatch")
    if connection.kind != request.kind:
        reasons.append("connection_kind_mismatch")
    if connection.binding_key != request.binding:
        reasons.append("connection_binding_mismatch")
    return reasons


def rules_broken(grant: CapabilityApproval, connection: Connection) -> bool:
    """The connection's schemas changed under the owner's rules. Fail closed."""
    definitions = connection.metadata.get("tools", {})
    return bool(grant.tool_rules and broken_rules(grant.tool_rules, definitions))


class Missing(StrEnum):
    """Why a run does not have a binding, in words safe to show the model."""

    NOT_CONNECTED = "not connected"
    WAITING_FOR_APPROVAL = "waiting for approval"
    DENIED = "denied"
    UNAVAILABLE = "not available"


def missing_reason(
    workspace: Workspace,
    request: RequestedCapability,
    grant: CapabilityApproval | None,
    reasons: list[str],
) -> Missing:
    if grant and grant.status == JobGrant.Status.DENIED:
        return Missing.DENIED
    connection = (grant.connection if grant else None) or Connection.objects.filter(
        workspace=workspace, binding_key=request.binding
    ).first()
    if connection is None or connection.status != Connection.Status.CONNECTED:
        return Missing.NOT_CONNECTED
    if {"no_grant", "grant_pending", "tools_not_granted"} & set(reasons):
        return Missing.WAITING_FOR_APPROVAL
    return Missing.UNAVAILABLE


def capability_plan(
    workspace: Workspace, repository: Repository, capability: PlannedCapability
) -> dict[str, Any]:
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
        reasons.append("tool_rules_broken")

    adapter = EXTERNAL_KINDS.get(request.kind)
    access = (
        adapter.access(connection, repository)
        if adapter and connection and connection.kind == request.kind
        else None
    )
    if access and not access.ok:
        reasons.append(access.reason)

    authorized = not reasons
    capability = {
        "binding": request.binding,
        "kind": request.kind,
        "required": request.required,
        "requested_tools": sorted(requested),
        "connection_id": connection.pk if connection else None,
        "connection_name": connection.display_name if connection else None,
        "effective_tools": sorted(effective) if authorized else [],
        "authorized": authorized,
        "reasons": reasons,
    }
    if not authorized:
        capability["missing"] = missing_reason(workspace, request, grant, reasons)
    if adapter and connection and access and access.scope and authorized:
        capability["external"] = adapter.freeze(connection, access.scope, effective)
        # Copied into the plan so a rule changed mid-run does not affect the run.
        capability["tool_rules"] = kept_rules(grant.tool_rules, effective)
    return capability


def compile_execution_plan(
    workspace: Workspace,
    repository: Repository,
    definition: ExecutionDefinition,
    resolved_capabilities: Sequence[PlannedCapability],
    limits: dict[str, int],
    commit_sha: str,
    pull_request: dict[str, Any] | None = None,
    issues: bool = False,
) -> dict[str, Any]:
    """Compile server-resolved policy into the existing versioned plan format."""
    if repository.workspace_id != workspace.pk:
        raise ValueError("Repository and execution policy must share a workspace.")
    capabilities = [
        capability_plan(workspace, repository, item) for item in resolved_capabilities
    ]
    ready = all(item["authorized"] for item in capabilities if item["required"])
    selection = workspace_execution(workspace)
    model = (
        model_plan(workspace) if selection.backend == AgentBackend.PYDANTIC else None
    )
    execution = {
        "backend": selection.backend,
        "label": selection.label,
        "ready": selection.ready,
        "problem": selection.problem,
    }
    if selection.connection is not None:
        connection = selection.connection
        execution |= {
            "connection_id": connection.pk,
            "credential_key": str(connection.credential_key),
            "owner_id": connection.owner_id,
        }
    ready = ready and selection.ready
    agent = agent_plan(
        AgentPolicy(
            max_turns=limits[RunLimit.MAX_TURNS],
            max_tool_calls=limits[RunLimit.MAX_TOOL_CALLS],
            max_tool_result_chars=settings.AGENT_MAX_TOOL_RESULT_CHARS,
        ),
    )
    # The gateway and the deadline read these from the plan, not from settings.
    agent["limits"] |= {
        RunLimit.TOKEN_BUDGET: limits[RunLimit.TOKEN_BUDGET],
        RunLimit.TIMEOUT_SECONDS: limits[RunLimit.TIMEOUT_SECONDS],
    }
    plan = {
        "version": 5,
        "execution": execution,
        "ready": ready,
        "agent": agent,
        "job": {
            "key": definition.key,
            "skill_path": definition.skill_path,
            "config_hash": definition.config_hash,
        },
        "repository": {
            "full_name": repository.full_name,
            "config_sha": commit_sha,
        },
        "policy": {"mode": "workspace-grants-v1"},
        "runner": {
            "image": settings.RUNNER_IMAGE,
            "network_policy": "none",
            # Only runs that publish the agent's changes need them recorded.
            "collect_changes": pull_request is not None,
        },
        # The draft pull request's base branch and CI choice, when asked for.
        "pull_request": pull_request,
        # The agent may propose this many issues. They open after the run.
        "issues": {"max": MAX_RUN_ISSUES} if issues else None,
        "model": model,
        "capabilities": capabilities,
    }
    plan["agent"]["tools"] = [tool.name for tool in authorized_tools(plan)]
    return deepcopy(plan)


def build_execution_plan(job: Job) -> dict[str, Any]:
    """Adapt a saved job and its grants without changing existing plan payloads."""
    capabilities = []
    for request in job.capability_requests.select_related("grant__connection").all():
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
        {
            "base": job.repository.default_branch,
            "run_ci": job.repository.pull_request_ci,
        }
        if job.pull_request
        else None,
        issues=job.issues,
    )


def build_skill_execution_plan(
    repository: Repository,
    definition: JobManifest | TaskManifest,
    *,
    key: str,
    commit_sha: str,
    pull_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    connections = {
        connection.binding_key: connection
        for connection in Connection.objects.filter(
            workspace_id=repository.workspace_id,
            binding_key__in=[request.binding for request in requests],
        )
    }
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
        {
            str(limit): workspace_value(repository.workspace, limit)
            for limit in RunLimit
        },
        commit_sha,
        pull_request,
    )
