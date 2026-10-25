"""The shared MCP client. Provider details come from the provider registry."""

import json
import re
from datetime import timedelta
from typing import Any

import anyio
import httpx
from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from secondshift.integrations.mcp.base import (
    MCPError,
    MCPProvider,
    MCPRequestError,
    MCPSettings,
)
from secondshift.integrations.mcp.oauth import fresh_connection
from secondshift.integrations.mcp.providers import connection_settings, mcp_provider
from secondshift.integrations.mcp.transport import (
    MCP_RESPONSE_MAX_BYTES,
    MCPTransport,
)
from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import host_label
from secondshift.integrations.services import ConnectionConflict
from secondshift.workspaces.models import Workspace

MCP_MAX_PAGES = 10
MCP_SCHEMA_MAX_DEPTH = 30
# Custom servers keep every tool for the owner to choose from, up to this many.
MCP_MAX_OFFERED_TOOLS = 200
AUTH_STRATEGIES = {
    "token": Connection.AuthStrategy.API_KEY,
    "oauth": Connection.AuthStrategy.OAUTH,
}


def validate_mcp_connection(connection: Connection) -> MCPProvider:
    """Fail closed unless the connection matches a built-in provider exactly."""
    provider = mcp_provider(connection.provider)
    if (
        connection.kind != Connection.Kind.MCP
        or connection.endpoint_url
        != provider.endpoint(provider.clean_settings(connection_settings(connection)))
        or connection.auth_strategy
        not in {AUTH_STRATEGIES[method] for method in provider.auth_methods}
        or connection.status != Connection.Status.CONNECTED
        or not connection.access_token
        or (connection.expires_at and connection.expires_at <= timezone.now())
    ):
        raise MCPError(f"This {provider.label} MCP connection is unavailable.")
    return provider


async def mcp_request(  # noqa: C901
    connection: Connection,
    tool: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """No token, HTTP exception text, or remote error text leaves this boundary."""
    provider = validate_mcp_connection(connection)
    try:
        with anyio.fail_after(settings.OUTBOUND_TIMEOUT_SECONDS):
            async with (
                httpx.AsyncClient(
                    transport=MCPTransport(),
                    headers={
                        **provider.headers,
                        "Authorization": (
                            f"{provider.auth_scheme} {connection.access_token}"
                        ),
                        "Accept-Encoding": "identity",
                    },
                    timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                streamable_http_client(connection.endpoint_url, http_client=client) as (
                    reader,
                    writer,
                    session_id,
                ),
                ClientSession(
                    reader,
                    writer,
                    read_timeout_seconds=timedelta(
                        seconds=settings.OUTBOUND_TIMEOUT_SECONDS
                    ),
                ) as session,
            ):
                del session_id
                await session.initialize()
                if tool is not None:
                    result = await session.call_tool(tool, arguments)
                    if result.isError:
                        raise MCPError(
                            f"{provider.label} could not complete this tool call."
                        )
                    payload = result.model_dump(mode="json", by_alias=True)
                else:
                    tools: dict[str, Any] = {}
                    cursor = None
                    page_count = 0
                    while page_count < MCP_MAX_PAGES:
                        page_count += 1
                        page = await session.list_tools(cursor=cursor)
                        for item in page.tools:
                            read_only = bool(
                                item.annotations and item.annotations.readOnlyHint
                            )
                            allowed = (
                                provider.owner_selects_tools
                                or item.name in provider.tools
                            )
                            if allowed and (
                                read_only or not provider.require_read_only_hint
                            ):
                                tools[item.name] = {
                                    "description": item.description or item.name,
                                    "inputSchema": item.inputSchema,
                                    "readOnly": read_only,
                                }
                        if len(tools) > MCP_MAX_OFFERED_TOOLS:
                            raise MCPError("The MCP server offers too many tools.")
                        cursor = page.nextCursor
                        if not cursor:
                            break
                    else:
                        raise MCPError("MCP tool discovery exceeded the page limit.")
                    if not tools:
                        raise MCPError(f"{provider.label} exposed no supported tools.")
                    payload = tools
    except Exception:
        # SDK exception groups can embed request headers or remote response text.
        raise MCPRequestError(
            f"{provider.label} MCP request failed. Check the token, permissions, "
            "and service availability."
        ) from None
    encoded = json.dumps(payload)
    if len(encoded.encode()) > MCP_RESPONSE_MAX_BYTES:
        raise MCPError("The MCP result exceeded the size limit.")
    return json.loads(encoded.replace(connection.access_token, "[redacted]"))


def validate_tool_schema(value: Any, depth: int = 0) -> None:
    if depth > MCP_SCHEMA_MAX_DEPTH:
        raise MCPError("The MCP schema is too deeply nested.")
    if isinstance(value, dict):
        if any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")):
            raise MCPError("MCP schema references are not supported.")
        for item in value.values():
            validate_tool_schema(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_tool_schema(item, depth + 1)


def discover_tools(connection: Connection) -> dict[str, Any]:
    fresh_connection(connection)
    tools = async_to_sync(mcp_request)(connection)
    for tool in tools.values():
        schema = tool["inputSchema"]
        validate_tool_schema(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:
            raise MCPError("The MCP server returned an invalid tool schema.") from None
        if schema.get("type") != "object":
            raise MCPError("MCP tool arguments must be an object.")
    return tools


def connection_target(provider: MCPProvider, cleaned: MCPSettings) -> str:
    """A label safe to show: provider and scope, or just the host for custom servers."""
    if provider.owner_selects_tools:
        return host_label(provider.endpoint(cleaned))
    return " · ".join(
        part for part in (f"{provider.label} MCP", "/".join(cleaned.values())) if part
    )


def select_tools(
    connection: Connection,
    provider: MCPProvider,
    tools: dict[str, Any],
    selected: list[str] | None = None,
) -> None:
    """Store what the server offers and what jobs may use.

    Built-in providers use every allowlisted tool. Custom servers use the
    owner's choice, starting with the tools the server marks read-only.
    """
    if not provider.owner_selects_tools:
        chosen = set(tools)
    elif selected is None:
        chosen = {name for name, tool in tools.items() if tool.get("readOnly")}
    else:
        unknown = set(selected) - set(tools)
        if unknown:
            raise MCPError("Choose tools from the server's list.")
        chosen = set(selected)
    connection.available_tools = sorted(chosen)
    connection.metadata = {
        **connection.metadata,
        "tools": {name: tools[name] for name in sorted(chosen)},
        "offered_tools": tools if provider.owner_selects_tools else {},
    }


def create_mcp_connection(
    workspace: Workspace,
    provider_key: str,
    binding_key: str,
    display_name: str,
    token: str,
    settings: dict[str, str] | None = None,
) -> Connection:
    provider = mcp_provider(provider_key)
    if "token" not in provider.auth_methods:
        raise MCPError(f"{provider.label} does not accept a pasted token.")
    if not re.fullmatch(provider.token_pattern, token):
        raise MCPError(provider.token_hint)
    cleaned = provider.clean_settings(settings or {})
    if Connection.objects.filter(workspace=workspace, binding_key=binding_key).exists():
        raise ConnectionConflict("This binding already has a connection.")
    connection = Connection(
        workspace=workspace,
        provider=provider.key,
        kind=Connection.Kind.MCP,
        binding_key=binding_key,
        display_name=display_name.strip() or provider.label,
        endpoint_url=provider.endpoint(cleaned),
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token=token,
        metadata={"target": connection_target(provider, cleaned), "settings": cleaned},
    )
    select_tools(connection, provider, discover_tools(connection))
    connection.full_clean()
    try:
        with transaction.atomic():
            connection.save()
    except IntegrityError:
        raise ConnectionConflict("This binding already has a connection.") from None
    return connection


def update_mcp_connection(  # noqa: C901
    connection: Connection,
    display_name: str | None,
    token: str | None,
    settings: dict[str, str] | None,
    tools: list[str] | None = None,
) -> Connection:
    """Replace the token, scope, or tool choice, then rediscover before saving.

    A blank token keeps the current one, unless the connection was revoked.
    Signed-in connections change their token or server only by signing in again.
    """
    if connection.kind != Connection.Kind.MCP:
        raise ConnectionConflict("Only MCP connections can be edited here.")
    provider = mcp_provider(connection.provider)
    if tools is not None and not provider.owner_selects_tools:
        raise MCPError(f"{provider.label} tools are fixed by SecondShift.")
    signed_in = connection.auth_strategy == Connection.AuthStrategy.OAUTH
    current = connection_settings(connection)
    if signed_in:
        if token or (settings is not None and settings != current):
            raise MCPError("Sign in again to change the server or credentials.")
        if connection.status != Connection.Status.CONNECTED:
            raise MCPError("Sign in again to reconnect.")
    elif token:
        if not re.fullmatch(provider.token_pattern, token):
            raise MCPError(provider.token_hint)
        connection.access_token = token
    elif connection.status == Connection.Status.REVOKED or not connection.access_token:
        raise MCPError("Enter a new token to reconnect.")
    cleaned = provider.clean_settings(current if settings is None else settings)
    if display_name is not None:
        connection.display_name = display_name.strip() or provider.label
    connection.endpoint_url = provider.endpoint(cleaned)
    connection.status = Connection.Status.CONNECTED
    connection.metadata = {
        **connection.metadata,
        "target": connection_target(provider, cleaned),
        "settings": cleaned,
    }
    # Raises before saving, so a bad token or scope leaves the stored one alone.
    offered = discover_tools(connection)
    if tools is None and provider.owner_selects_tools:
        tools = [name for name in connection.available_tools if name in offered]
    select_tools(connection, provider, offered, tools)
    connection.full_clean()
    connection.save()
    return connection
