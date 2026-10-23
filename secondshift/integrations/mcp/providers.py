from secondshift.integrations.mcp.base import MCPError, MCPProvider
from secondshift.integrations.mcp.github import GITHUB_MCP

MCP_PROVIDERS: dict[str, MCPProvider] = {GITHUB_MCP.key: GITHUB_MCP}


def mcp_provider(key: str) -> MCPProvider:
    try:
        return MCP_PROVIDERS[key]
    except KeyError:
        raise MCPError("This MCP provider is not supported.") from None
