from dataclasses import dataclass
from typing import Any

LOCAL_TOOLS = (
    "workspace.list_files",
    "workspace.read_file",
    "workspace.search_text",
    "workspace.run_command",
)
EXTERNAL_KINDS = {"api", "mcp"}


class ToolPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class AuthorizedTool:
    name: str
    kind: str
    binding: str | None
    provider_tool: str | None

    @property
    def is_local(self) -> bool:
        return self.kind == "workspace"


def external_tool_name(kind: str, binding: str, provider_tool: str) -> str:
    """Namespace provider tools so bindings cannot collide in an agent turn."""
    if kind not in EXTERNAL_KINDS:
        raise ToolPolicyError(f"Unsupported external tool kind: {kind}")
    if not binding or not provider_tool:
        raise ToolPolicyError("External tools require a binding and tool name.")
    return f"{kind}.{binding}.{provider_tool}"


def authorized_tools(execution_plan: dict[str, Any]) -> tuple[AuthorizedTool, ...]:
    """Compile the only tool names that an agent may request for this run."""
    tools = [
        AuthorizedTool(name=name, kind="workspace", binding=None, provider_tool=None)
        for name in LOCAL_TOOLS
    ]
    if not execution_plan.get("ready", False):
        return tuple(tools)

    for capability in execution_plan.get("capabilities", []):
        kind = str(capability.get("kind", ""))
        if not capability.get("authorized") or kind not in EXTERNAL_KINDS:
            continue
        binding = str(capability.get("binding", ""))
        for provider_tool in capability.get("effective_tools", []):
            provider_tool = str(provider_tool)
            tools.append(
                AuthorizedTool(
                    name=external_tool_name(kind, binding, provider_tool),
                    kind=kind,
                    binding=binding,
                    provider_tool=provider_tool,
                )
            )

    by_name = {tool.name: tool for tool in tools}
    if len(by_name) != len(tools):
        raise ToolPolicyError("The execution plan produced duplicate tool names.")
    return tuple(by_name[name] for name in sorted(by_name))


def resolve_tool(execution_plan: dict[str, Any], name: str) -> AuthorizedTool:
    """Resolve a model-supplied name only through the frozen run policy."""
    matches = {tool.name: tool for tool in authorized_tools(execution_plan)}
    try:
        return matches[name]
    except KeyError as error:
        raise ToolPolicyError(f"Tool is not authorized for this run: {name}") from error
