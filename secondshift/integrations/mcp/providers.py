from secondshift.integrations.mcp.base import MCPError, MCPProvider
from secondshift.integrations.mcp.github import GITHUB_MCP
from secondshift.repositories.models import Repository

MCP_PROVIDERS: dict[str, MCPProvider] = {GITHUB_MCP.key: GITHUB_MCP}


def mcp_provider(key: str) -> MCPProvider:
    try:
        return MCP_PROVIDERS[key]
    except KeyError:
        raise MCPError("This MCP provider is not supported.") from None


def mcp_scopes(repository: Repository) -> dict[str, str | None]:
    """What a job in this repository could reach through each provider."""
    return {key: provider.scope(repository) for key, provider in MCP_PROVIDERS.items()}


def missing_scope_message(connection_provider: str, repository: Repository) -> str:
    """Why a job cannot use this provider, or an empty string when it can."""
    provider = MCP_PROVIDERS.get(connection_provider)
    if provider is None:
        return "This MCP provider is not supported."
    return "" if provider.scope(repository) else provider.missing_scope_message
