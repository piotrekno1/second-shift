import hashlib
import re
from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext, Tool, ToolFailed

from secondshift.connections.errors import ExternalRequestError
from secondshift.jobs.tool_policy import authorized_tools
from secondshift.runs.execution.deadlines import (
    check_deadline,
)
from secondshift.runs.execution.tool_broker import (
    broker_connection,
    call_external_tool_step,
)
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps
from secondshift.runs.models import Run

MODEL_TOOL_NAME_HASH_LENGTH = 8


def model_tool_name(prefix: str, provider_tool: str, name: str) -> str:
    """Within the model's 64-character limit, stable for the same tool."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", provider_tool)[:40]
    digest = hashlib.sha256(name.encode()).hexdigest()[:MODEL_TOOL_NAME_HASH_LENGTH]
    return f"{prefix}_{safe}_{digest}"


def external_tools(run: Run) -> tuple[Tool[WorkspaceToolDeps], ...]:
    tools = []
    for tool in authorized_tools(run.execution_plan):
        if tool.is_local:
            continue
        authorization = broker_connection(run, tool.name)

        def wrapper(name: str) -> Callable[..., Any]:
            def call(
                ctx: RunContext[WorkspaceToolDeps], **arguments: Any
            ) -> dict[str, Any]:
                if not ctx.tool_call_id:
                    raise RuntimeError("The model tool call has no stable identifier.")
                check_deadline(ctx.deps.deadline)
                try:
                    return call_external_tool_step(
                        ctx.deps.run_id, name, arguments, ctx.tool_call_id
                    )
                except ExternalRequestError as error:
                    raise ToolFailed(str(error)) from None

            return call

        tools.append(
            Tool.from_schema(
                wrapper(tool.name),
                name=model_tool_name(
                    authorization.name_prefix, str(tool.provider_tool), tool.name
                ),
                description=(
                    f"{tool.name}. Approved {authorization.scope_label}: "
                    f"{authorization.scope}. {authorization.definition['description']}"
                ),
                json_schema=authorization.definition["inputSchema"],
                takes_ctx=True,
                sequential=True,
            )
        )
    return tuple(tools)
