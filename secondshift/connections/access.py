"""What a job could reach through an external connection."""

from dataclasses import dataclass
from enum import StrEnum


class AccessReason(StrEnum):
    """Why a job cannot use an external connection. Stored in run plans."""

    MCP_PROVIDER_UNSUPPORTED = "mcp_provider_unsupported"
    MCP_SCOPE_UNKNOWN = "mcp_scope_unknown"
    MCP_SERVER_UNKNOWN = "mcp_server_unknown"
    GITHUB_REPOSITORY_UNKNOWN = "github_repository_unknown"
    SENTRY_ORGANIZATION_UNKNOWN = "sentry_organization_unknown"
    MCP_SOURCE_MISSING = "mcp_source_missing"
    API_BASE_URL_UNKNOWN = "api_base_url_unknown"


@dataclass(frozen=True)
class Access:
    """What a job could reach through a connection, in plain words."""

    ok: bool
    scope: str | None
    text: str
    # Set when the job cannot use the connection.
    reason: AccessReason | None = None
