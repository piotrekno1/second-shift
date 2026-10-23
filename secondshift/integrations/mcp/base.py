import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from secondshift.repositories.models import Repository

MCPSettings = dict[str, str]


class MCPError(ValueError):
    pass


class MCPRequestError(MCPError):
    """A remote failure the agent may report as unavailable evidence."""


@dataclass(frozen=True)
class MCPSetting:
    """A value the owner enters when connecting, such as a Sentry organization."""

    key: str
    label: str
    # Matched in full. Keep it to simple character classes so browsers agree.
    pattern: str
    required: bool = True
    help: str = ""
    placeholder: str = ""
    max_length: int = 64
    # Extra check after the pattern, such as an outbound URL check.
    validate: Callable[[str], str] | None = None


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
    scope: Callable[[MCPSettings, Repository], str | None]
    # Forces model arguments into the approved scope, or raises MCPError.
    scope_arguments: Callable[[str, str, dict[str, Any]], dict[str, Any]]
    # Completes "Allow these tools to read ...".
    describe_scope: Callable[[str], str]
    access_verb: str = "read"
    scope_label: str = "scope"
    missing_scope_reason: str = "mcp_scope_unknown"
    missing_scope_message: str = "This job has no scope for this provider."
    settings: tuple[MCPSetting, ...] = ()
    # Appended to endpoint_url, built only from validated settings.
    endpoint_path: Callable[[MCPSettings], str] = lambda settings: ""
    auth_scheme: str = "Bearer"
    # Fixed request headers, sent alongside the token.
    headers: dict[str, str] = field(default_factory=dict)
    # Drop tools the server does not mark read-only.
    require_read_only_hint: bool = False
    # Custom servers: keep every tool and let the owner choose. Read-only
    # tools start selected.
    owner_selects_tools: bool = False
    # "token" for a pasted bearer token, "oauth" for a browser sign-in.
    auth_methods: tuple[str, ...] = ("token",)
    token_pattern: str = r"\S+"
    token_hint: str = "Enter a token without whitespace."
    # Shown when an owner sets up a connection.
    description: str = ""
    default_binding: str = ""
    token_label: str = "Access token"
    setup_url: str = ""
    setup_help: str = ""

    def endpoint(self, settings: MCPSettings) -> str:
        return self.endpoint_url + self.endpoint_path(settings)

    def clean_settings(self, values: dict[str, str]) -> MCPSettings:
        """Keep only declared settings, each matching its pattern."""
        known = {setting.key for setting in self.settings}
        if set(values) - known:
            raise MCPError(f"{self.label} does not accept these settings.")
        cleaned: MCPSettings = {}
        for setting in self.settings:
            value = values.get(setting.key, "").strip()
            if not value:
                if setting.required:
                    raise MCPError(f"Enter the {setting.label.lower()}.")
                continue
            if len(value) > setting.max_length or not re.fullmatch(
                setting.pattern, value
            ):
                raise MCPError(f"The {setting.label.lower()} is not valid.")
            if setting.validate:
                value = setting.validate(value)
            cleaned[setting.key] = value
        return cleaned
