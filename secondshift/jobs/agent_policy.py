from dataclasses import dataclass
from typing import Any

AGENT_DRIVER = "pydantic-ai"
AGENT_PROTOCOL = "pydantic-ai-v1"


@dataclass(frozen=True)
class AgentPolicy:
    max_turns: int
    max_tool_calls: int
    max_tool_result_chars: int = 65_536

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls cannot be negative")
        if self.max_tool_result_chars < 1:
            raise ValueError("max_tool_result_chars must be positive")


def agent_plan(limits: AgentPolicy) -> dict[str, Any]:
    """Return the server-owned, redacted agent portion of an execution plan."""
    return {
        "driver": AGENT_DRIVER,
        "protocol": AGENT_PROTOCOL,
        "limits": {
            "max_turns": limits.max_turns,
            "max_tool_calls": limits.max_tool_calls,
            "max_tool_result_chars": limits.max_tool_result_chars,
        },
    }
