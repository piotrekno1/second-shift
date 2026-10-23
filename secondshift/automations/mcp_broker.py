"""Orchestrator-side MCP calls. Only redacted inputs cross durable steps."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync
from dbos import DBOS
from jsonschema import ValidationError, validate

from secondshift.automations.models import JobGrant, Run, RunEvent
from secondshift.automations.tool_policy import resolve_tool
from secondshift.integrations.mcp.base import MCPError, MCPProvider
from secondshift.integrations.mcp.client import mcp_request, validate_mcp_connection
from secondshift.integrations.models import Connection


@dataclass(frozen=True)
class MCPAuthorization:
    connection: Connection
    provider: MCPProvider
    schema: dict[str, Any]
    scope: str


def broker_connection(run: Run, name: str) -> MCPAuthorization:
    tool = resolve_tool(run.execution_plan, name)
    if tool.kind != Connection.Kind.MCP:
        raise MCPError("This external tool is not supported.")
    capability = next(
        item
        for item in run.execution_plan["capabilities"]
        if item["kind"] == tool.kind and item["binding"] == tool.binding
    )
    grant = (
        JobGrant.objects.select_related("connection")
        .filter(
            capability_request__job=run.job,
            capability_request__kind=tool.kind,
            capability_request__binding=tool.binding,
            connection_id=capability.get("connection_id"),
            status=JobGrant.Status.APPROVED,
        )
        .first()
    )
    if (
        run.status in {Run.Status.CANCELED, Run.Status.FAILED, Run.Status.SUCCEEDED}
        or not grant
        or not grant.connection
        or grant.connection.workspace_id != run.job.workspace_id
        or grant.connection.binding_key != tool.binding
        or tool.provider_tool not in grant.allowed_tools
        or tool.provider_tool not in grant.capability_request.requested_tools
        or tool.provider_tool not in grant.connection.available_tools
    ):
        raise MCPError("Access to this MCP tool is no longer approved.")
    connection = grant.connection
    provider = validate_mcp_connection(connection)
    frozen = capability.get("mcp", {})
    if (
        frozen.get("provider") != provider.key
        or not frozen.get("scope")
        or frozen["scope"] != provider.scope(run.job.repository)
    ):
        raise MCPError(
            f"The job {provider.scope_label} scope changed. Start a new run."
        )
    schema = frozen.get("tools", {}).get(tool.provider_tool)
    if not schema or schema != connection.metadata.get("tools", {}).get(
        tool.provider_tool
    ):
        raise MCPError("The MCP tool definition changed. Start a new run.")
    return MCPAuthorization(connection, provider, schema, frozen["scope"])


@DBOS.step(name="secondshift.call_mcp_tool")
def call_mcp_tool_step(
    run_id: int, name: str, arguments: dict[str, Any], call_id: str
) -> dict[str, Any]:
    run = Run.objects.select_related("job").get(pk=run_id)
    authorization = broker_connection(run, name)
    tool = resolve_tool(run.execution_plan, name)
    try:
        validate(arguments, authorization.schema["inputSchema"])
    except ValidationError:
        raise MCPError("The MCP tool arguments do not match its schema.") from None
    scoped = authorization.provider.scope_arguments(
        authorization.scope, str(tool.provider_tool), arguments
    )
    evidence = {
        "tool": name,
        "connection_id": authorization.connection.pk,
        "call_id": hashlib.sha256(call_id.encode()).hexdigest(),
    }
    try:
        result = async_to_sync(mcp_request)(
            authorization.connection, tool.provider_tool, scoped
        )
    except MCPError:
        RunEvent.objects.create(
            run=run,
            level="warning",
            message=f"MCP tool failed: {name}",
            metadata={**evidence, "ok": False},
        )
        raise
    # Never record arguments, credentials, or remote payloads in audit events.
    RunEvent.objects.create(
        run=run,
        message=f"MCP tool completed: {name}",
        metadata={
            **evidence,
            "ok": True,
            "result_bytes": len(json.dumps(result).encode()),
        },
    )
    limit = int(run.execution_plan["agent"]["limits"]["max_tool_result_chars"])
    text = json.dumps(result)
    if len(text) > limit:
        return {"output": text[:limit], "output_truncated": True}
    return result
