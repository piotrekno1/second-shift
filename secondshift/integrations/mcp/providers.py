from dataclasses import dataclass

from secondshift.integrations.mcp.base import MCPError, MCPProvider
from secondshift.integrations.mcp.github import GITHUB_MCP
from secondshift.integrations.mcp.sentry import SENTRY_MCP
from secondshift.integrations.models import Connection
from secondshift.repositories.models import Repository

MCP_PROVIDERS: dict[str, MCPProvider] = {
    provider.key: provider for provider in (GITHUB_MCP, SENTRY_MCP)
}


def mcp_provider(key: str) -> MCPProvider:
    try:
        return MCP_PROVIDERS[key]
    except KeyError:
        raise MCPError("This MCP provider is not supported.") from None


@dataclass(frozen=True)
class MCPAccess:
    """What a job could read through one MCP connection, in plain words."""

    ok: bool
    scope: str | None
    text: str


def connection_settings(connection: Connection) -> dict[str, str]:
    return dict(connection.metadata.get("settings", {}))


def connection_scope(connection: Connection, repository: Repository) -> str | None:
    provider = mcp_provider(connection.provider)
    return provider.scope(connection_settings(connection), repository)


def mcp_access(connection: Connection, repository: Repository) -> MCPAccess:
    provider = MCP_PROVIDERS.get(connection.provider)
    if provider is None:
        return MCPAccess(False, None, "This MCP provider is not supported.")
    scope = provider.scope(connection_settings(connection), repository)
    if not scope:
        return MCPAccess(False, None, provider.missing_scope_message)
    return MCPAccess(
        True, scope, f"Allow these tools to read {provider.describe_scope(scope)}."
    )
