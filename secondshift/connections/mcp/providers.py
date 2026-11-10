from secondshift.connections.mcp.base import MCPError, MCPProvider
from secondshift.connections.mcp.custom import CUSTOM_MCP
from secondshift.connections.mcp.github import GITHUB_MCP
from secondshift.connections.mcp.sentry import SENTRY_MCP

MCP_PROVIDERS: dict[str, MCPProvider] = {
    provider.key: provider for provider in (GITHUB_MCP, SENTRY_MCP, CUSTOM_MCP)
}


def mcp_provider(key: str) -> MCPProvider:
    try:
        return MCP_PROVIDERS[key]
    except KeyError:
        raise MCPError("This MCP provider is not supported.") from None
