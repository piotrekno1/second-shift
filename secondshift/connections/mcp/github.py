"""GitHub's hosted MCP server: read-only tools, one repository per job."""

import re
from typing import Any

from secondshift.config.constants import (
    GITHUB_MCP_TOOLS,
    GITHUB_MCP_URL,
    GITHUB_REPOSITORY_PATTERN,
    GitHubMCPTool,
)
from secondshift.connections.mcp.base import MCPError, MCPProvider, MCPSettings
from secondshift.connections.models import Connection
from secondshift.repositories.github import github_repository_scope
from secondshift.repositories.models import Repository

# Fine-grained tokens are limited to chosen repositories. Classic tokens are not.
GITHUB_FINE_GRAINED_TOKEN_PATTERN = r"github_pat_[A-Za-z0-9_]+"
# Search text is written by the model. A short limit keeps it a search.
GITHUB_SEARCH_QUERY_MAX_CHARS = 256
# Artifacts and log archives are left out. They return download links.
GITHUB_ACTIONS_METHODS = {
    GitHubMCPTool.ACTIONS_LIST: frozenset(
        {"list_workflows", "list_workflow_runs", "list_workflow_jobs"}
    ),
    GitHubMCPTool.ACTIONS_GET: frozenset(
        {"get_workflow", "get_workflow_run", "get_workflow_job"}
    ),
}
# The end of a failed job's log usually holds the error.
GITHUB_JOB_LOG_MAX_LINES = 300


def scoped_search_query(repository: str, query: str) -> str:
    """Pin a pull request search to the job's repository."""
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
    return f"repo:{repository} {query.strip()}".strip()


def job_log_lines(requested: Any) -> int:
    """The requested log tail, capped. Anything else gets the cap."""
    if not isinstance(requested, int) or isinstance(requested, bool) or requested < 1:
        return GITHUB_JOB_LOG_MAX_LINES
    return min(requested, GITHUB_JOB_LOG_MAX_LINES)


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
        scoped["query"] = scoped_search_query(repository, str(scoped.get("query", "")))
    methods = GITHUB_ACTIONS_METHODS.get(tool)
    if methods is not None and scoped.get("method") not in methods:
        raise MCPError(f"Use one of these methods: {', '.join(sorted(methods))}.")
    if tool == GitHubMCPTool.GET_JOB_LOGS:
        # A download link would skip the broker's size limit and redaction.
        scoped["return_content"] = True
        scoped["tail_lines"] = job_log_lines(scoped.get("tail_lines"))
    return scoped


def narrowed_github_schema(tool: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Show the model only the Actions methods and log options it may use.

    A call outside the scope rules stops the run, so the model must not be
    offered methods those rules block.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    narrowed = {**schema, "properties": dict(properties)}
    methods = GITHUB_ACTIONS_METHODS.get(tool)
    if methods is not None and isinstance(properties.get("method"), dict):
        narrowed["properties"]["method"] = {
            **properties["method"],
            "enum": sorted(methods),
        }
    if tool == GitHubMCPTool.GET_JOB_LOGS:
        # Always set by scope_arguments, so the model has no choice to make.
        narrowed["properties"].pop("return_content", None)
        if isinstance(narrowed.get("required"), list):
            narrowed["required"] = [
                name for name in narrowed["required"] if name != "return_content"
            ]
        if isinstance(properties.get("tail_lines"), dict):
            # GitHub's own default is above the cap.
            narrowed["properties"]["tail_lines"] = {
                **{
                    key: value
                    for key, value in properties["tail_lines"].items()
                    if key != "default"
                },
                "description": (
                    "Lines to return from the end of the log. "
                    f"At most {GITHUB_JOB_LOG_MAX_LINES}."
                ),
            }
    return narrowed


def github_scope(settings: MCPSettings, repository: Repository) -> str | None:
    """A job reads only its own repository, whatever the token can reach."""
    del settings
    return github_repository_scope(repository)


def describe_github_scope(scope: str) -> str:
    return f"the {scope} repository"


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
    scope=github_scope,
    describe_scope=describe_github_scope,
    scope_label="repository",
    missing_scope_reason="github_repository_unknown",
    missing_scope_message=(
        "This job has no GitHub repository identity. For a local checkout, "
        "configure a GitHub origin and resync it."
    ),
    description="Read pull requests and commits. Each job reads only its own repository.",
    default_binding="github",
    token_label="Fine-grained personal access token",
    setup_url="https://github.com/settings/personal-access-tokens/new",
    setup_help=(
        "Pick your jobs' repositories and grant read-only Pull requests, "
        "Commit statuses, and Contents."
    ),
    scope_arguments=scoped_github_arguments,
    narrow_schema=narrowed_github_schema,
)
