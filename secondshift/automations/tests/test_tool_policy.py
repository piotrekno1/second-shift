import pytest

from secondshift.automations.tool_policy import (
    LOCAL_TOOLS,
    ToolPolicyError,
    authorized_tools,
    external_tool_name,
    resolve_tool,
)


def plan(*capabilities: dict[str, object], ready: bool = True) -> dict[str, object]:
    return {"ready": ready, "capabilities": list(capabilities)}


def test_policy_includes_local_and_only_effective_external_tools() -> None:
    execution_plan = plan(
        {
            "kind": "mcp",
            "binding": "observability",
            "authorized": True,
            "effective_tools": ["query_metrics"],
        },
        {
            "kind": "api",
            "binding": "billing",
            "authorized": False,
            "effective_tools": ["refund"],
        },
        {
            "kind": "notification",
            "binding": "alerts",
            "authorized": True,
            "effective_tools": ["deliver"],
        },
    )

    tools = authorized_tools(execution_plan)

    assert {tool.name for tool in tools} == {
        *LOCAL_TOOLS,
        "mcp.observability.query_metrics",
    }
    external = resolve_tool(execution_plan, "mcp.observability.query_metrics")
    assert not external.is_local
    assert external.binding == "observability"
    assert external.provider_tool == "query_metrics"


def test_blocked_plan_exposes_no_external_tools() -> None:
    execution_plan = plan(
        {
            "kind": "mcp",
            "binding": "observability",
            "authorized": True,
            "effective_tools": ["query_metrics"],
        },
        ready=False,
    )

    assert {tool.name for tool in authorized_tools(execution_plan)} == set(LOCAL_TOOLS)
    with pytest.raises(ToolPolicyError, match="not authorized"):
        resolve_tool(execution_plan, "mcp.observability.query_metrics")


@pytest.mark.parametrize("kind", ["notification", "model", "workspace"])
def test_external_names_reject_non_brokered_kinds(kind: str) -> None:
    with pytest.raises(ToolPolicyError, match="Unsupported"):
        external_tool_name(kind, "binding", "tool")
