"""External tools by connection kind: MCP servers and HTTP APIs.

The planner, broker, and access review are the same for every kind. Each kind
supplies only what differs: what a job may reach, what is frozen into a run
plan, how a connection is checked against that plan, and how a call is made.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from asgiref.sync import async_to_sync

from secondshift.connections.access import Access, AccessReason
from secondshift.connections.errors import ExternalToolError
from secondshift.connections.http_api.client import (
    build_request,
    send,
    validate_api_connection,
)
from secondshift.connections.mcp.client import (
    borrows_source,
    mcp_request,
    validate_mcp_connection,
)
from secondshift.connections.mcp.oauth import fresh_connection
from secondshift.connections.mcp.providers import (
    MCP_PROVIDERS,
    mcp_provider,
)
from secondshift.connections.models import Connection
from secondshift.connections.outbound import host_label
from secondshift.connections.repository_ref import RepositoryRef


@dataclass(frozen=True)
class Authorization:
    """A connection checked against the run's frozen plan, ready to call."""

    connection: Connection
    scope: str
    # The frozen tool definition: description and inputSchema at least.
    definition: dict[str, Any]
    # Start of the model-facing tool name, such as "github" or "api".
    name_prefix: str
    scope_label: str
    # The run's repository. Connections that borrow its source need it.
    repository: RepositoryRef | None = None


class ExternalKind(Protocol):
    def access(self, connection: Connection, repository: RepositoryRef) -> Access: ...

    def freeze(
        self, connection: Connection, scope: str, tools: set[str]
    ) -> dict[str, Any]: ...

    def authorize(
        self,
        connection: Connection,
        frozen: dict[str, Any],
        repository: RepositoryRef,
        tool: str,
    ) -> Authorization: ...

    def call(
        self, authorization: Authorization, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...


def frozen_tools(connection: Connection, tools: set[str]) -> dict[str, Any]:
    definitions = connection.tool_definitions
    return {name: definitions.get(name) for name in sorted(tools)}


def frozen_definition(
    connection: Connection, frozen: dict[str, Any], tool: str, noun: str
) -> dict[str, Any]:
    definition = frozen.get("tools", {}).get(tool)
    if not definition or definition != connection.tool_definitions.get(tool):
        raise ExternalToolError(f"The {noun} definition changed. Start a new run.")
    return definition


class MCPKind:
    def access(self, connection: Connection, repository: RepositoryRef) -> Access:
        provider = MCP_PROVIDERS.get(connection.provider)
        if provider is None:
            return Access(
                False,
                None,
                "This MCP provider is not supported.",
                AccessReason.MCP_PROVIDER_UNSUPPORTED,
            )
        scope = provider.scope(connection.settings, repository)
        if not scope:
            return Access(
                False,
                None,
                provider.missing_scope_message,
                provider.missing_scope_reason,
            )
        if borrows_source(connection) and not provider.source_reachable(repository):
            return Access(
                False,
                None,
                provider.missing_source_message,
                AccessReason.MCP_SOURCE_MISSING,
            )
        return Access(
            True,
            scope,
            f"Allow these tools to {provider.access_verb} "
            f"{provider.describe_scope(scope)}.",
        )

    def freeze(
        self, connection: Connection, scope: str, tools: set[str]
    ) -> dict[str, Any]:
        """The approved scope and tool definitions. Never credentials."""
        return {
            "provider": connection.provider,
            "scope": scope,
            "tools": frozen_tools(connection, tools),
        }

    def authorize(
        self,
        connection: Connection,
        frozen: dict[str, Any],
        repository: RepositoryRef,
        tool: str,
    ) -> Authorization:
        # Signed-in connections renew their token here, before the checks below.
        fresh_connection(connection)
        provider = validate_mcp_connection(connection)
        if (
            frozen.get("provider") != provider.key
            or not frozen.get("scope")
            or frozen["scope"] != provider.scope(connection.settings, repository)
        ):
            raise ExternalToolError(
                f"The job {provider.scope_label} scope changed. Start a new run."
            )
        return Authorization(
            connection=connection,
            scope=frozen["scope"],
            definition=frozen_definition(connection, frozen, tool, "MCP tool"),
            name_prefix=provider.key,
            scope_label=provider.scope_label,
            repository=repository,
        )

    def call(
        self, authorization: Authorization, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        connection = authorization.connection
        provider = mcp_provider(connection.provider)
        scoped = provider.scope_arguments(authorization.scope, tool, arguments)
        token = ""
        if borrows_source(connection):
            if provider.source_token is None or authorization.repository is None:
                raise ExternalToolError("This MCP connection has no repository.")
            # Minted for this call and this repository only. Never stored.
            token = provider.source_token(authorization.repository)
        return async_to_sync(mcp_request)(connection, tool, scoped, token)


class APIKind:
    def access(self, connection: Connection, repository: RepositoryRef) -> Access:
        del repository
        base_url = connection.endpoint_url
        if not base_url:
            return Access(
                False,
                None,
                "This API connection has no base URL.",
                AccessReason.API_BASE_URL_UNKNOWN,
            )
        host = host_label(base_url)
        return Access(
            True,
            host,
            f"Allow these operations to use anything the credential can reach on {host}.",
        )

    def freeze(
        self, connection: Connection, scope: str, tools: set[str]
    ) -> dict[str, Any]:
        return {
            "base_url": connection.endpoint_url,
            "scope": scope,
            "tools": frozen_tools(connection, tools),
        }

    def authorize(
        self,
        connection: Connection,
        frozen: dict[str, Any],
        repository: RepositoryRef,
        tool: str,
    ) -> Authorization:
        del repository
        validate_api_connection(connection)
        if not frozen.get("base_url") or frozen["base_url"] != connection.endpoint_url:
            raise ExternalToolError("The API base URL changed. Start a new run.")
        return Authorization(
            connection=connection,
            scope=frozen["scope"],
            definition=frozen_definition(connection, frozen, tool, "API operation"),
            name_prefix="api",
            scope_label="API",
        )

    def call(
        self, authorization: Authorization, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del tool
        request = build_request(
            authorization.connection.endpoint_url, authorization.definition, arguments
        )
        return async_to_sync(send)(authorization.connection, request)


EXTERNAL_KINDS: dict[str, ExternalKind] = {
    Connection.Kind.MCP: MCPKind(),
    Connection.Kind.API: APIKind(),
}


def external_kind(kind: str) -> ExternalKind:
    try:
        return EXTERNAL_KINDS[kind]
    except KeyError:
        raise ExternalToolError("This external tool is not supported.") from None


def external_access(connection: Connection, repository: RepositoryRef) -> Access | None:
    """None for connections that are not external tools, such as webhooks."""
    adapter = EXTERNAL_KINDS.get(connection.kind)
    return adapter.access(connection, repository) if adapter else None


def changing_tools(connection: Connection, tools: list[str]) -> list[str]:
    """Requested tools that may change data, for the access review.

    Built-in MCP providers only offer allowlisted read tools. Custom servers
    and HTTP APIs rely on the server's own read-only labels and HTTP methods.
    """
    if connection.kind == Connection.Kind.MCP:
        provider = MCP_PROVIDERS.get(connection.provider)
        if provider is None or not provider.owner_selects_tools:
            return []
    elif connection.kind != Connection.Kind.API:
        return []
    offered = connection.offered_tools
    return [
        name for name in tools if name in offered and not offered[name].get("readOnly")
    ]
