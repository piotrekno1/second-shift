from typing import Any

from django.conf import settings

from secondshift.automations.agent import AgentLimits, agent_plan
from secondshift.automations.limits import RunLimit, effective_limits
from secondshift.automations.models import CapabilityRequest, Job, JobGrant
from secondshift.automations.tool_policy import authorized_tools
from secondshift.integrations.external import EXTERNAL_KINDS
from secondshift.integrations.model_providers import provider_family
from secondshift.integrations.models import Connection


def model_plan(job: Job) -> dict[str, Any] | None:
    """The workspace's default model provider, frozen into the run's plan."""
    connection = Connection.objects.filter(
        workspace_id=job.workspace_id,
        kind=Connection.Kind.MODEL,
        is_default=True,
        status=Connection.Status.CONNECTED,
    ).first()
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


def blocking_reasons(
    job: Job,
    request: CapabilityRequest,
    grant: JobGrant | None,
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
        if connection.status != Connection.Status.CONNECTED:
            reasons.append(f"connection_{connection.status}")
        if connection.workspace_id != job.workspace_id:
            reasons.append("connection_workspace_mismatch")
        if connection.kind != request.kind:
            reasons.append("connection_kind_mismatch")
        if connection.binding_key != request.binding:
            reasons.append("connection_binding_mismatch")
    if grant and requested - set(grant.allowed_tools):
        reasons.append("tools_not_granted")
    if connection and requested - set(connection.available_tools):
        reasons.append("tools_unavailable")
    return reasons


def capability_plan(job: Job, request: CapabilityRequest) -> dict[str, Any]:
    """One requested capability, with the tools the run may use."""
    grant = getattr(request, "grant", None)
    connection = grant.connection if grant else None
    requested = set(request.requested_tools)
    allowed = set(grant.allowed_tools) if grant else set()
    available = set(connection.available_tools) if connection else set()
    effective = requested & allowed & available
    reasons = blocking_reasons(job, request, grant, connection)

    adapter = EXTERNAL_KINDS.get(request.kind)
    access = (
        adapter.access(connection, job.repository)
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
    if adapter and connection and access and access.scope and authorized:
        capability["external"] = adapter.freeze(connection, access.scope, effective)
    return capability


def build_execution_plan(job: Job) -> dict[str, Any]:
    """Compile the effective, redacted authorization plan for a future run."""
    requests = job.capability_requests.select_related("grant__connection").all()
    capabilities = [capability_plan(job, request) for request in requests]
    ready = all(item["authorized"] for item in capabilities if item["required"])
    model = model_plan(job)
    if model is None:
        ready = False
    limits = effective_limits(job)
    agent = agent_plan(
        AgentLimits(
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
        "version": 4,
        "ready": ready,
        "agent": agent,
        "job": {
            "key": job.key,
            "skill_path": job.skill_path,
            "config_hash": job.config_hash,
        },
        "repository": {
            "full_name": job.repository.full_name,
            "config_sha": job.repository.config_sha,
        },
        "policy": {"mode": "workspace-grants-v1"},
        "runner": {
            "image": settings.RUNNER_IMAGE,
            "network_policy": "none",
        },
        "model": model,
        "capabilities": capabilities,
    }
    plan["agent"]["tools"] = [tool.name for tool in authorized_tools(plan)]
    return plan
