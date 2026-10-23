from typing import Any

from django.conf import settings

from secondshift.automations.agent import AgentLimits, agent_plan
from secondshift.automations.models import Job, JobGrant
from secondshift.automations.tool_policy import authorized_tools
from secondshift.integrations.mcp.base import MCPProvider
from secondshift.integrations.mcp.providers import MCP_PROVIDERS
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


def mcp_capability_plan(
    connection: Connection,
    provider: MCPProvider,
    scope: str | None,
    effective_tools: set[str],
) -> dict[str, Any]:
    """Freeze the approved scope and tool definitions without credentials."""
    definitions = connection.metadata.get("tools", {})
    return {
        "provider": provider.key,
        "scope": scope,
        "tools": {name: definitions.get(name) for name in sorted(effective_tools)},
    }


def build_execution_plan(job: Job) -> dict[str, Any]:
    """Compile the effective, redacted authorization plan for a future run."""
    capabilities: list[dict[str, Any]] = []
    ready = True
    requests = job.capability_requests.select_related("grant__connection").all()

    for request in requests:
        grant = getattr(request, "grant", None)
        connection = grant.connection if grant else None
        requested = set(request.requested_tools)
        allowed = set(grant.allowed_tools) if grant else set()
        available = set(connection.available_tools) if connection else set()
        effective = requested & allowed & available

        reasons: list[str] = []
        if not grant:
            reasons.append("no_grant")
        elif grant.status != JobGrant.Status.APPROVED:
            reasons.append(f"grant_{grant.status}")
        if connection and connection.status != Connection.Status.CONNECTED:
            reasons.append(f"connection_{connection.status}")
        if connection and connection.workspace_id != job.workspace_id:
            reasons.append("connection_workspace_mismatch")
        if connection and connection.kind != request.kind:
            reasons.append("connection_kind_mismatch")
        if connection and connection.binding_key != request.binding:
            reasons.append("connection_binding_mismatch")
        if grant and requested - allowed:
            reasons.append("tools_not_granted")
        if connection and requested - available:
            reasons.append("tools_unavailable")

        provider = None
        scope = None
        if connection and request.kind == Connection.Kind.MCP:
            provider = MCP_PROVIDERS.get(connection.provider)
            if provider is None:
                reasons.append("mcp_provider_unsupported")
            else:
                scope = provider.scope(job.repository)
                if not scope:
                    reasons.append(provider.missing_scope_reason)

        authorized = not reasons
        if request.required and not authorized:
            ready = False
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
        if connection and provider and authorized:
            capability["mcp"] = mcp_capability_plan(
                connection, provider, scope, effective
            )
        capabilities.append(capability)

    model = model_plan(job)
    if model is None:
        ready = False
    plan = {
        "version": 2,
        "ready": ready,
        "agent": agent_plan(
            AgentLimits(
                max_turns=settings.AGENT_MAX_TURNS,
                max_tool_calls=settings.AGENT_MAX_TOOL_CALLS,
                max_tool_result_chars=settings.AGENT_MAX_TOOL_RESULT_CHARS,
            ),
        ),
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
