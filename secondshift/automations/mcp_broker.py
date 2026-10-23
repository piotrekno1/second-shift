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
from secondshift.integrations.github_mcp import (
    GitHubMCPError,
    github_mcp_request,
    scoped_github_arguments,
    validate_mcp_connection,
)
from secondshift.integrations.models import Connection
from secondshift.repositories.github import github_repository_scope


@dataclass(frozen=True)
class MCPAuthorization:
    connection: Connection
    schema: dict[str, Any]
    repository_scope: str


def broker_connection(run: Run, name: str) -> MCPAuthorization:
    tool = resolve_tool(run.execution_plan, name)
    if tool.kind != Connection.Kind.MCP:
        raise GitHubMCPError("This external tool is not supported.")
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
        raise GitHubMCPError("Access to this MCP tool is no longer approved.")
    connection = grant.connection
    validate_mcp_connection(connection)
    frozen = capability.get("mcp", {})
    if not frozen.get("repository") or frozen["repository"] != github_repository_scope(
        run.job.repository
    ):
        raise GitHubMCPError("The job repository scope changed. Start a new run.")
    schema = frozen.get("tools", {}).get(tool.provider_tool)
    if not schema or schema != connection.metadata.get("tools", {}).get(
        tool.provider_tool
    ):
        raise GitHubMCPError("The MCP tool definition changed. Start a new run.")
    return MCPAuthorization(connection, schema, frozen["repository"])


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
        raise GitHubMCPError(
            "The MCP tool arguments do not match its schema."
        ) from None
    scoped = scoped_github_arguments(
        authorization.repository_scope, str(tool.provider_tool), arguments
    )
    evidence = {
        "tool": name,
        "connection_id": authorization.connection.pk,
        "call_id": hashlib.sha256(call_id.encode()).hexdigest(),
    }
    try:
        result = async_to_sync(github_mcp_request)(
            authorization.connection, tool.provider_tool, scoped
        )
    except GitHubMCPError:
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
