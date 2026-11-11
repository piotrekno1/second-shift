"""Let the agent say what access it needed and this run did not have.

A skill from another repository often expects services nobody connected. The
agent cannot see tools it was not given, so it reports them here instead of
quietly doing less. The run page lists them. Reporting never grants anything.
"""

from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import ModelRetry, RunContext, Tool

from secondshift.runs.dao import (
    access_need_for_call,
    create_access_need,
    run_access_needs,
    update_access_need_tools,
)
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps

TOOL_NAME = "report_missing_access"
# Enough for a skill that names several services, without filling the page.
MAX_SERVICES = 20
MAX_TOOLS = 30

ServiceArgument = Annotated[str, Field(min_length=1, max_length=100)]
ToolsArgument = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=100)]],
    Field(max_length=MAX_TOOLS),
]
ReasonArgument = Annotated[str, Field(max_length=1000)]

ACCESS_NOTE = f"""If the task needs a service, tool or credential this run does not have,
such as an MCP server or API the skill names, call {TOOL_NAME} once for each
service, then go on with what you can do. Do not try to reach the service
another way. Name the missing access in your report too."""


def report_missing_access(
    ctx: RunContext[WorkspaceToolDeps],
    service: ServiceArgument,
    tools: ToolsArgument | None = None,
    reason: ReasonArgument = "",
) -> dict[str, Any]:
    """Record a service or tool this task needs and this run was not given.

    Name the service, such as linear or sentry, the tools needed from it, such
    as create_issue, and why. People grant access after the run. Nothing is
    granted now.
    """
    if not ctx.tool_call_id:
        raise RuntimeError("The model tool call has no stable identifier.")
    service = " ".join(service.split())
    if not service:
        raise ModelRetry("Name the service.")
    names = sorted({" ".join(tool.split()) for tool in tools or []} - {""})
    run_id = ctx.deps.run_id
    if access_need_for_call(run_id, ctx.tool_call_id):
        return recorded()
    needs = run_access_needs(run_id)
    same = next(
        (need for need in needs if need.service.casefold() == service.casefold()),
        None,
    )
    if same is not None:
        merged = sorted(set(same.tools) | set(names))[:MAX_TOOLS]
        if merged != same.tools:
            update_access_need_tools(same, merged)
        return recorded()
    if len(needs) >= MAX_SERVICES:
        return {
            "recorded": False,
            "note": f"This run already reported {MAX_SERVICES} services. "
            "Name the rest in the report.",
        }
    create_access_need(run_id, ctx.tool_call_id, service, names, reason.strip())
    return recorded()


def recorded() -> dict[str, Any]:
    return {
        "recorded": True,
        "note": "Recorded for people to review after the run. It is not "
        "available in this run. Continue without it.",
    }


def access_tool() -> Tool[WorkspaceToolDeps]:
    return Tool(report_missing_access, name=TOOL_NAME)
