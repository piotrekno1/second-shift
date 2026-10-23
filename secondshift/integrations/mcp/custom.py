"""Any MCP server the owner names. SecondShift cannot scope what it reaches."""

from typing import Any

from secondshift.integrations.mcp.base import (
    MCPError,
    MCPProvider,
    MCPSetting,
    MCPSettings,
)
from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import (
    OutboundUrlError,
    host_label,
    validate_outbound_url,
)
from secondshift.repositories.models import Repository

URL = "url"
# Connection.endpoint_url is a URLField with Django's default limit.
CUSTOM_URL_MAX_LENGTH = 200
OAUTH = "oauth"
TOKEN = "token"


def custom_server_url(value: str) -> str:
    """HTTPS only, no credentials in the URL. DNS is checked when connecting."""
    try:
        return validate_outbound_url(value, "server URL", resolve=False)
    except OutboundUrlError as error:
        raise MCPError(str(error)) from None


def custom_scope(settings: MCPSettings, repository: Repository) -> str | None:
    del repository
    url = settings.get(URL)
    return host_label(url) if url else None


def custom_arguments(
    scope: str, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    # The owner chose the tools. Schema checks still run in the broker.
    del scope, tool
    return dict(arguments)


def describe_custom_scope(scope: str) -> str:
    return f"anything the credential can reach on {scope}"


CUSTOM_MCP = MCPProvider(
    key=Connection.Provider.MCP,
    label="Custom server",
    endpoint_url="",
    tools=(),
    scope=custom_scope,
    scope_arguments=custom_arguments,
    describe_scope=describe_custom_scope,
    access_verb="use",
    scope_label="server",
    missing_scope_reason="mcp_server_unknown",
    missing_scope_message="This MCP connection has no server URL.",
    settings=(
        MCPSetting(
            key=URL,
            label="Server URL",
            pattern=r"https?://\S+",
            max_length=CUSTOM_URL_MAX_LENGTH,
            help="The server's MCP endpoint, usually ending in /mcp.",
            placeholder="https://mcp.example.com/mcp",
            validate=custom_server_url,
        ),
    ),
    endpoint_path=lambda settings: settings.get(URL, ""),
    owner_selects_tools=True,
    auth_methods=(OAUTH, TOKEN),
    token_hint="Enter a bearer token without whitespace.",
    description=(
        "Connect any MCP server that supports sign-in or a bearer token. "
        "You choose which tools jobs may use."
    ),
    token_label="Bearer token",
    setup_help="Sent as Authorization: Bearer <token>.",
)
