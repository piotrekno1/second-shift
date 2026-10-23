from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from secondshift.repositories.models import Repository


class MCPError(ValueError):
    pass


class MCPRequestError(MCPError):
    """A remote failure the agent may report as unavailable evidence."""


@dataclass(frozen=True)
class MCPProvider:
    """Everything SecondShift trusts about one built-in MCP server.

    The endpoint, tools and scoping rules are server-owned. Repository config
    and model output can never change them.
    """

    key: str
    label: str
    endpoint_url: str
    # Only these tools are discovered, approved, and called.
    tools: tuple[str, ...]
    # What a job may touch, such as "owner/repo". None means it cannot run.
    scope: Callable[[Repository], str | None]
    # Forces model arguments into the approved scope, or raises MCPError.
    scope_arguments: Callable[[str, str, dict[str, Any]], dict[str, Any]]
    scope_label: str = "scope"
    missing_scope_reason: str = "mcp_scope_unknown"
    # Fixed request headers, sent alongside the bearer token.
    headers: dict[str, str] = field(default_factory=dict)
    token_pattern: str = r"\S+"
    token_hint: str = "Enter a token without whitespace."
