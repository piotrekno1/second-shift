"""The control plane sends only fixed agent choices, never Docker options."""

from typing import Any, Literal

from ninja import Schema
from pydantic import ConfigDict, Field, SecretStr


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


class AgentCheckInput(Schema):
    """A login check. The runner owns the prompt and every limit."""

    model_config = ConfigDict(extra="forbid")
    backend: Literal["claude_code", "codex"]
    credential_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    auth: SecretStr = Field(max_length=64000, exclude=True)


class AgentOutput(Schema):
    report: str = ""
    succeeded: bool = False
    reason: str = "agent_failed"
    usage: dict[str, int] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()
