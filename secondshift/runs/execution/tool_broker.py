"""Orchestrator-side calls to external tools. Only redacted inputs cross durable steps."""

import hashlib
import json
from typing import Any

from dbos import DBOS
from jsonschema import ValidationError, validate

from secondshift.connections.errors import ExternalToolError
from secondshift.connections.external import Authorization, external_kind
from secondshift.jobs.models import JobGrant
from secondshift.jobs.tool_policy import resolve_tool
from secondshift.runs.models import Run, RunEvent

FINISHED = {Run.Status.CANCELED, Run.Status.FAILED, Run.Status.SUCCEEDED}


def broker_connection(run: Run, name: str) -> Authorization:
    """Check the grant and the connection against the run's frozen plan."""
    tool = resolve_tool(run.execution_plan, name)
    adapter = external_kind(tool.kind)
    capability = next(
        item
        for item in run.execution_plan["capabilities"]
        if item["kind"] == tool.kind and item["binding"] == tool.binding
    )
    grant = (
        JobGrant.objects.select_related("connection", "capability_request")
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
        run.status in FINISHED
        or not grant
        or not grant.connection
        or grant.connection.kind != tool.kind
        or grant.connection.workspace_id != run.job.workspace_id
        or not grant.connection.serves(tool.binding)
        or tool.provider_tool not in grant.allowed_tools
        or tool.provider_tool not in grant.capability_request.requested_tools
        or tool.provider_tool not in grant.connection.available_tools
    ):
        raise ExternalToolError("Access to this tool is no longer approved.")
    return adapter.authorize(
        grant.connection,
        capability.get("external", {}),
        run.job.repository,
        str(tool.provider_tool),
    )


@DBOS.step(name="secondshift.call_external_tool")
def call_external_tool_step(
    run_id: int, name: str, arguments: dict[str, Any], call_id: str
) -> dict[str, Any]:
    run = Run.objects.select_related("job__repository").get(pk=run_id)
    authorization = broker_connection(run, name)
    tool = resolve_tool(run.execution_plan, name)
    try:
        validate(arguments, authorization.definition["inputSchema"])
    except ValidationError:
        raise ExternalToolError("The tool arguments do not match its schema.") from None
    evidence = {
        "tool": name,
        "connection_id": authorization.connection.pk,
        "call_id": hashlib.sha256(call_id.encode()).hexdigest(),
    }
    try:
        result = external_kind(tool.kind).call(
            authorization, str(tool.provider_tool), arguments
        )
    except ExternalToolError:
        RunEvent.objects.create(
            run=run,
            level="warning",
            message=f"Tool failed: {name}",
            metadata={**evidence, "ok": False},
        )
        raise
    # Never record arguments, credentials, or remote payloads in audit events.
    RunEvent.objects.create(
        run=run,
        message=f"Tool completed: {name}",
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
