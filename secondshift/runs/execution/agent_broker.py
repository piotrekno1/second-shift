"""Stateless MCP transport over the existing, run-scoped tool policy."""

import hashlib
import json
from typing import Any

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from pydantic_ai import ModelRetry, RunContext, RunUsage, Tool, ToolFailed

from secondshift.connections.agents import usable_agent_connection
from secondshift.connections.models import AgentConnection
from secondshift.runs.artifacts import run_payload
from secondshift.runs.execution.deadlines import check_deadline, run_deadline
from secondshift.runs.execution.external_tools import external_tools
from secondshift.runs.execution.payload import payload_tool
from secondshift.runs.execution.workspace_tools import (
    MODEL_TOOL_NAMES,
    WorkspaceToolDeps,
    workspace_tools,
)
from secondshift.runs.models import AgentLease, Run

# Streamable HTTP MCP versions the broker speaks, newest first.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")


class AgentAccessError(ValueError):
    pass


class MethodNotFound(ValueError):
    """Clients probe newer methods first and fall back when they are missing."""


def run_connection(run: Run) -> AgentConnection:
    plan = run.execution_plan["execution"]
    connection = AgentConnection.objects.filter(
        pk=plan["connection_id"],
        workspace_id=run.workspace_id,
        owner_id=plan["owner_id"],
        credential_key=plan["credential_key"],
        backend=plan["backend"],
    ).first()
    if connection is None or not usable_agent_connection(connection, run.workspace_id):
        raise AgentAccessError(
            "The agent login is no longer available. Reconnect and start a new run."
        )
    return connection


def lease_run(lease: AgentLease) -> Run:
    run = Run.objects.select_related("job").get(pk=lease.run_id)
    if not lease.active or run.status != Run.Status.RUNNING:
        raise AgentAccessError("This agent run is no longer active.")
    check_deadline(run_deadline(run))
    run_connection(run)
    return run


def broker_tools(run: Run) -> dict[str, Tool[WorkspaceToolDeps]]:
    allowed = set(run.execution_plan["agent"]["tools"])
    local = {MODEL_TOOL_NAMES[name] for name in allowed if name in MODEL_TOOL_NAMES}
    tools = [tool for tool in workspace_tools() if tool.name in local]
    if run_payload(run):
        tools.append(payload_tool())
    return {tool.name: tool for tool in (*tools, *external_tools(run))}


def tool_result(
    lease: AgentLease, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    with transaction.atomic():
        lease = AgentLease.objects.select_for_update().get(pk=lease.pk)
        run = lease_run(lease)
        if lease.tool_calls >= int(
            run.execution_plan["agent"]["limits"]["max_tool_calls"]
        ):
            raise AgentAccessError("This run has used its tool call budget.")
        # Resolve and validate before counting the call.
        tool = broker_tools(run).get(name)
        if tool is None:
            raise AgentAccessError("This tool is not approved for the run.")
        validated = tool.function_schema.validator.validate_python(arguments)
        lease.tool_calls += 1
        lease.save(update_fields=["tool_calls"])
    # The broker numbers calls itself. Clients restart their request IDs when
    # they reconnect, so those IDs cannot tell two calls apart.
    call_id = f"agent-{lease.pk}-{lease.tool_calls}"
    deps = WorkspaceToolDeps(
        run_id=run.pk,
        session_id=lease.session_id,
        max_output_chars=int(
            run.execution_plan["agent"]["limits"]["max_tool_result_chars"]
        ),
        deadline=run_deadline(run),
        allowed_tools=tuple(run.execution_plan["agent"]["tools"]),
        payload=run_payload(run),
    )
    context = RunContext(deps=deps, model=None, usage=RunUsage(), tool_call_id=call_id)
    try:
        value = tool.function(context, **validated)
        return {
            "content": [{"type": "text", "text": json.dumps(value)}],
            "isError": False,
        }
    except (ModelRetry, ToolFailed) as error:
        # SecondShift wrote these for the agent, for example which argument to fix.
        return {"content": [{"type": "text", "text": error.message}], "isError": True}
    except Exception:
        # Provider exceptions can include request contents. Return no raw errors.
        return {
            "content": [
                {"type": "text", "text": "The approved tool could not complete."}
            ],
            "isError": True,
        }


def mcp_request(lease: AgentLease, message: dict[str, Any]) -> dict[str, Any] | None:
    run = lease_run(lease)
    method = message.get("method")
    if "id" not in message:
        return None
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        return {
            "protocolVersion": requested
            if requested in PROTOCOL_VERSIONS
            else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "secondshift-run", "version": "1.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "inputSchema": tool.function_schema.json_schema,
                }
                for tool in broker_tools(run).values()
            ]
        }
    if method == "tools/call":
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise AgentAccessError("Invalid tool arguments.")
        return tool_result(lease, str(params.get("name", "")), arguments)
    raise MethodNotFound(str(method))


@csrf_exempt
def agent_mcp(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    lease = (
        AgentLease.objects.filter(
            token_hash=hashlib.sha256(token.encode()).hexdigest(), active=True
        ).first()
        if token
        else None
    )
    if lease is None:
        return HttpResponse(status=401)
    if len(request.body) > 1024 * 1024:
        return HttpResponse(status=413)
    message: dict[str, Any] = {}
    try:
        message = json.loads(request.body)
        if not isinstance(message, dict):
            return HttpResponse(status=400)
        result = mcp_request(lease, message)
        if "id" not in message:
            return HttpResponse(status=202)
        return JsonResponse({"jsonrpc": "2.0", "id": message["id"], "result": result})
    except MethodNotFound:
        return JsonResponse(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32601, "message": "Method not found."},
            }
        )
    except Exception:
        return JsonResponse(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {
                    "code": -32000,
                    "message": "Agent access denied or tool request invalid.",
                },
            }
        )
