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


class AgentOutcome(StrEnum):
    COMPLETED = "completed"
    RECONNECT_REQUIRED = "reconnect_required"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TIMED_OUT = "timed_out"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    TURN_BUDGET_EXHAUSTED = "turn_budget_exhausted"
    AGENT_OUTPUT_LIMIT = "agent_output_limit"
    INVALID_AGENT_OUTPUT = "invalid_agent_output"
    AGENT_FAILED = "agent_failed"


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


def numbers(values: dict[str, Any]) -> dict[str, int]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


def private_file(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o600)
