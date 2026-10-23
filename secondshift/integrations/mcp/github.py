"""GitHub's hosted MCP server: read-only tools, one repository per job."""

import re
from typing import Any

from secondshift.config.constants import (
    GITHUB_MCP_TOOLS,
    GITHUB_MCP_URL,
    GITHUB_REPOSITORY_PATTERN,
    GitHubMCPTool,
)
from secondshift.integrations.mcp.base import MCPError, MCPProvider
from secondshift.integrations.models import Connection
from secondshift.repositories.github import github_repository_scope

# Fine-grained tokens are limited to chosen repositories. Classic tokens are not.
GITHUB_FINE_GRAINED_TOKEN_PATTERN = r"github_pat_[A-Za-z0-9_]+"
# Search text is written by the model. A short limit keeps it a search.
GITHUB_SEARCH_QUERY_MAX_CHARS = 256


def scoped_github_arguments(
    repository: str, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Enforce the owner's repository scope independently of model instructions."""
    if tool not in GITHUB_MCP_TOOLS:
        raise MCPError("This GitHub tool is not supported.")
    if not re.fullmatch(GITHUB_REPOSITORY_PATTERN, repository):
        raise MCPError("The job has no valid GitHub repository scope.")
    owner, repo = repository.split("/", 1)
    scoped = dict(arguments)
    for key, value in (("owner", owner), ("repo", repo)):
        if key in scoped and scoped[key] != value:
            raise MCPError("The tool must target the approved repository.")
        scoped[key] = value
    if tool == GitHubMCPTool.LIST_COMMITS and scoped.get("sha"):
        raise MCPError("List commits from the default branch by omitting sha.")
    if tool == GitHubMCPTool.SEARCH_PULL_REQUESTS:
        query = str(scoped.get("query", ""))
        if len(query) > GITHUB_SEARCH_QUERY_MAX_CHARS:
            raise MCPError(
                f"Keep the search under {GITHUB_SEARCH_QUERY_MAX_CHARS} characters."
            )
        # OR can widen a repo-qualified search. Disallow it rather than attempt
        # to rewrite the provider's query language. GitHub only reads it in capitals.
        if re.search(r"\bOR\b", query):
            raise MCPError("Use separate searches instead of OR.")
        query = re.sub(rf"(?i)(?<!\S)repo:{re.escape(repository)}(?=\s|$)", "", query)
        if re.search(r"repo\s*:", query, re.IGNORECASE):
            raise MCPError("The search must target the approved repository.")
        scoped["query"] = f"repo:{repository} {query.strip()}".strip()
    return scoped


GITHUB_MCP = MCPProvider(
    key=Connection.Provider.GITHUB,
    label="GitHub",
    endpoint_url=GITHUB_MCP_URL,
    tools=GITHUB_MCP_TOOLS,
    headers={
        "X-MCP-Tools": ",".join(GITHUB_MCP_TOOLS),
        "X-MCP-Readonly": "true",
    },
    token_pattern=GITHUB_FINE_GRAINED_TOKEN_PATTERN,
    token_hint="Enter a fine-grained GitHub token. It starts with github_pat_.",
    scope=github_repository_scope,
    scope_label="repository",
    missing_scope_reason="github_repository_unknown",
    scope_arguments=scoped_github_arguments,
)
