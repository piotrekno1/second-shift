"""What the agent image shares between its CLI backends, and why a run ended.

The agent image ships this file next to agent.py, so it may use only the
standard library. The control plane imports AgentOutcome from here too.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

# Container paths. Backends read them as outcomes.TMP and outcomes.AUTH, so
# tests can point both somewhere else.
TMP = Path("/tmp")
AUTH = Path("/auth")
MAX_REPORT_CHARS = 200_000
TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
# Cache reads cost about a tenth of fresh input on Anthropic and OpenAI, so
# they count as a tenth toward a run's token budget.
CACHE_READ_DIVISOR = 10


class AgentOutcome(StrEnum):
    COMPLETED = "completed"
    RECONNECT_REQUIRED = "reconnect_required"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TIMED_OUT = "timed_out"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    TURN_BUDGET_EXHAUSTED = "turn_budget_exhausted"
    SUBAGENT_REFUSED = "subagent_refused"
    AGENT_OUTPUT_LIMIT = "agent_output_limit"
    INVALID_AGENT_OUTPUT = "invalid_agent_output"
    AGENT_FAILED = "agent_failed"
    # The agent's container ended without a result, for example because it ran
    # out of memory or was stopped. The wrapper itself always exits cleanly.
    AGENT_STOPPED = "agent_stopped"


@dataclass
class Outcome:
    report: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    # The CLI gave a final answer.
    finished: bool = False
    # Set when a limit stopped the CLI early.
    reason: str = ""
    # The last error the CLI reported, used to explain a failure.
    error: str = ""
    # Token usage of each model reply so far, by message ID.
    replies: dict[str, dict[str, int]] = field(default_factory=dict)
    # Set for runs with sub-agents: the most model replies, theirs included,
    # and the most sub-agents.
    max_replies: int | None = None
    max_subagents: int = 0
    # Whether each sub-agent start the agent asked for is allowed, by tool call.
    starts: dict[str, bool] = field(default_factory=dict)
    # The model replies and tool calls of each sub-agent that ran, in order.
    subagents: dict[str, dict[str, int]] = field(default_factory=dict)
    # Tool calls already counted, by ID. One call can arrive more than once.
    tool_calls: set[str] = field(default_factory=set)


def numbers(values: dict[str, Any]) -> dict[str, int]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


def private_file(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o600)


def budget_count(fresh: int, cached: int) -> int:
    """Tokens counted toward a run's budget. Cache reads count as a tenth,
    rounded up."""
    return fresh + (cached + CACHE_READ_DIVISOR - 1) // CACHE_READ_DIVISOR


def cache_reads(usage: dict[str, int]) -> int:
    """Cached input from CLI usage. Claude Code and Codex name it differently."""
    return usage.get("cache_read_input_tokens", 0) + usage.get("cached_input_tokens", 0)


def usage_budget(usage: dict[str, int]) -> int:
    """Tokens counted toward a run's budget, from CLI usage."""
    # Codex counts cached input inside its input tokens.
    fresh_input = max(
        usage.get("input_tokens", 0) - usage.get("cached_input_tokens", 0), 0
    )
    fresh = (
        fresh_input
        + usage.get("output_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
    )
    return budget_count(fresh, cache_reads(usage))
