"""Which agent runs a job, and how its container reaches the gateway."""

from enum import StrEnum
from urllib.parse import urlsplit

# How agent containers reach services on the Docker host.
HOST_ALIAS = "host.docker.internal"
# The gateway's MCP endpoint for subscription agents. Deploy files spell it out too.
AGENT_BROKER_PATH = "gateway/agent/mcp"


def local_agent_broker_url(gateway_url: str) -> str:
    """The gateway's agent broker, as agent containers on this host reach it."""
    port = urlsplit(gateway_url).port or 80
    return f"http://{HOST_ALIAS}:{port}/{AGENT_BROKER_PATH}"


class AgentBackend(StrEnum):
    """Who runs the agent: SecondShift's own loop, or a subscription CLI."""

    PYDANTIC = "pydantic"
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"

    @property
    def uses_model_api(self) -> bool:
        """Runs call a model provider through SecondShift's gateway. The CLIs
        call their provider directly with the subscription login."""
        return self is AgentBackend.PYDANTIC

    @property
    def starts_subagents(self) -> bool:
        """Runs can start sub-agents when the job allows them."""
        return self is not AgentBackend.CODEX

    @property
    def limits_model_calls(self) -> bool:
        """Runs stop at the run's model-call limit."""
        return self is not AgentBackend.CODEX
