"""Sentry's hosted MCP server, limited to one organization or project."""

from typing import Any

from secondshift.config.constants import (
    SENTRY_MCP_TOOLS,
    SENTRY_MCP_URL,
    SENTRY_SLUG_PATTERN,
    SentryMCPTool,
)
from secondshift.connections.mcp.base import (
    MCPError,
    MCPProvider,
    MCPSetting,
    MCPSettings,
)
from secondshift.connections.models import Connection
from secondshift.repositories.models import Repository

ORGANIZATION = "organization"
PROJECT = "project"
# Sentry's hosted server reads a pasted token from this scheme. Plain Bearer is
# reserved for its own OAuth tokens.
SENTRY_AUTH_SCHEME = "Sentry-Bearer"
# Search text is written by the model and read by Sentry's search agent.
SENTRY_QUERY_MAX_CHARS = 256
# Replays and agent conversations are left out. They often hold personal data.
SENTRY_RESOURCE_TYPES = {"issue", "event", "trace"}


def sentry_scope(settings: MCPSettings, repository: Repository) -> str | None:
    """The owner chose the reach when connecting. Every job shares it."""
    del repository
    organization = settings.get(ORGANIZATION)
    if not organization:
        return None
    project = settings.get(PROJECT)
    return f"{organization}/{project}" if project else organization


def sentry_endpoint_path(settings: MCPSettings) -> str:
    # Sentry limits its tools to this organization or project.
    return "".join(
        f"/{settings[key]}" for key in (ORGANIZATION, PROJECT) if key in settings
    )


def describe_sentry_scope(scope: str) -> str:
    organization, _, project = scope.partition("/")
    if project:
        return f"the {organization}/{project} Sentry project"
    return f"all Sentry projects in {organization}"


def scoped_sentry_arguments(
    scope: str, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Keep model arguments inside the approved organization or project.

    The endpoint already limits Sentry to the scope. These checks stop the
    model from naming another place to read.
    """
    if tool not in SENTRY_MCP_TOOLS:
        raise MCPError("This Sentry tool is not supported.")
    # A region URL picks the Sentry host. The scoped endpoint finds it itself.
    if arguments.get("regionUrl"):
        raise MCPError("Leave regionUrl empty. The approved scope sets the region.")
    _, _, project = scope.partition("/")
    for key in ("projectSlug", "projectSlugOrId"):
        value = arguments.get(key)
        if project and value and value != project:
            raise MCPError("The tool must target the approved Sentry project.")
    query = arguments.get("query")
    if isinstance(query, str) and len(query) > SENTRY_QUERY_MAX_CHARS:
        raise MCPError(f"Keep the search under {SENTRY_QUERY_MAX_CHARS} characters.")
    if tool == SentryMCPTool.GET_SENTRY_RESOURCE:
        # A URL could name any organization. Look resources up by ID instead.
        if arguments.get("url"):
            raise MCPError("Fetch Sentry resources by resourceType and resourceId.")
        if arguments.get("resourceType") not in SENTRY_RESOURCE_TYPES:
            raise MCPError("Only issues, events, and traces can be fetched.")
        if not arguments.get("resourceId"):
            raise MCPError("Fetch Sentry resources by resourceType and resourceId.")
    return dict(arguments)


SENTRY_MCP = MCPProvider(
    key=Connection.Provider.SENTRY,
    label="Sentry",
    endpoint_url=SENTRY_MCP_URL,
    tools=SENTRY_MCP_TOOLS,
    scope=sentry_scope,
    scope_arguments=scoped_sentry_arguments,
    describe_scope=describe_sentry_scope,
    scope_label="Sentry scope",
    missing_scope_reason="sentry_organization_unknown",
    missing_scope_message="This Sentry connection has no organization.",
    settings=(
        MCPSetting(
            key=ORGANIZATION,
            label="Organization",
            pattern=SENTRY_SLUG_PATTERN,
            help="The organization slug from your Sentry URL.",
            placeholder="acme",
        ),
        MCPSetting(
            key=PROJECT,
            label="Project",
            pattern=SENTRY_SLUG_PATTERN,
            required=False,
            help="Optional. Leave empty to allow every project in the organization.",
            placeholder="backend",
        ),
    ),
    endpoint_path=sentry_endpoint_path,
    auth_scheme=SENTRY_AUTH_SCHEME,
    require_read_only_hint=True,
    token_pattern=r"[A-Za-z0-9_=.+/-]+",
    token_hint="Enter a Sentry auth token without whitespace.",
    description="Read issues, errors, and traces from one organization or project.",
    default_binding="sentry",
    token_label="Auth token",
    setup_url="https://sentry.io/settings/account/api/auth-tokens/",
    setup_help="Use only org:read, project:read, and event:read. Write scopes are not checked.",
)
