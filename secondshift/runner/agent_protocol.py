"""The control plane sends only fixed agent choices, never Docker options."""

from typing import Literal

from ninja import Schema
from pydantic import ConfigDict, Field, SecretStr

from secondshift.runner.agent_image.outcomes import AgentOutcome


class AgentInput(Schema):
    model_config = ConfigDict(extra="forbid")
    backend: Literal["claude_code", "codex"]
    credential_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    auth: SecretStr = Field(max_length=64000, exclude=True)
    broker_token: SecretStr = Field(max_length=200, exclude=True)
    prompt: str = Field(max_length=250000)
    max_turns: int = Field(ge=1, le=10000)
    token_budget: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1, le=86400)
    # The most sub-agents the agent may start. Only Claude Code can so far.
    subagents: int = Field(default=0, ge=0, le=100)


class AgentCheckInput(Schema):
    """A login check. The runner owns the prompt and every limit."""

    model_config = ConfigDict(extra="forbid")
    backend: Literal["claude_code", "codex"]
    credential_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    auth: SecretStr = Field(max_length=64000, exclude=True)


class AgentOutput(Schema):
    report: str = ""
    succeeded: bool = False
    reason: str = AgentOutcome.AGENT_FAILED
    usage: dict[str, int] = Field(default_factory=dict)
    # Each sub-agent's model requests and tool calls, in the order they started.
    subagents: list[dict[str, int]] = Field(default_factory=list, max_length=100)
    versions: dict[str, str] = Field(default_factory=dict)
