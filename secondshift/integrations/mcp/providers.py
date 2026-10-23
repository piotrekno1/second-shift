from secondshift.integrations.mcp.base import MCPError, MCPProvider
from secondshift.integrations.mcp.custom import CUSTOM_MCP
from secondshift.integrations.mcp.github import GITHUB_MCP
from secondshift.integrations.mcp.sentry import SENTRY_MCP
from secondshift.integrations.models import Connection
from secondshift.repositories.models import Repository

MCP_PROVIDERS: dict[str, MCPProvider] = {
    provider.key: provider for provider in (GITHUB_MCP, SENTRY_MCP, CUSTOM_MCP)
}


def mcp_provider(key: str) -> MCPProvider:
    try:
        return MCP_PROVIDERS[key]
    except KeyError:
        raise MCPError("This MCP provider is not supported.") from None


def connection_settings(connection: Connection) -> dict[str, str]:
    return dict(connection.metadata.get("settings", {}))


def connection_scope(connection: Connection, repository: Repository) -> str | None:
    provider = mcp_provider(connection.provider)
    return provider.scope(connection_settings(connection), repository)
