"""The shared MCP client. Provider details come from the provider registry."""

import json
import re
from collections.abc import AsyncIterator
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

from secondshift.core.models import Workspace
from secondshift.integrations.mcp.base import MCPError, MCPProvider, MCPRequestError
from secondshift.integrations.mcp.providers import connection_settings, mcp_provider
from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import PinnedTransport
from secondshift.integrations.services import ConnectionConflict

MCP_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
MCP_MAX_PAGES = 10
MCP_SCHEMA_MAX_DEPTH = 30


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream) -> None:
        self.inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.inner:
            size += len(chunk)
            if size > MCP_RESPONSE_MAX_BYTES:
                raise MCPError("The MCP response exceeded the size limit.")
            yield chunk

    async def aclose(self) -> None:
        await self.inner.aclose()


class MCPTransport(PinnedTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise MCPError("Compressed MCP responses are not supported.")
        response.stream = BoundedStream(response.stream)
        return response


def validate_mcp_connection(connection: Connection) -> MCPProvider:
    """Fail closed unless the connection matches a built-in provider exactly."""
    provider = mcp_provider(connection.provider)
    if (
        connection.kind != Connection.Kind.MCP
        or connection.endpoint_url
        != provider.endpoint(provider.clean_settings(connection_settings(connection)))
        or connection.auth_strategy != Connection.AuthStrategy.API_KEY
        or connection.status != Connection.Status.CONNECTED
        or not connection.access_token
        or (connection.expires_at and connection.expires_at <= timezone.now())
    ):
        raise MCPError(f"This {provider.label} MCP connection is unavailable.")
    return provider


async def mcp_request(
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
                            if item.name in provider.tools and (
                                read_only or not provider.require_read_only_hint
                            ):
                                tools[item.name] = {
                                    "description": item.description or item.name,
                                    "inputSchema": item.inputSchema,
                                }
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


def create_mcp_connection(
    workspace: Workspace,
    provider_key: str,
    binding_key: str,
    display_name: str,
    token: str,
    settings: dict[str, str] | None = None,
) -> Connection:
    provider = mcp_provider(provider_key)
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
        metadata={
            "target": " · ".join(
                part
                for part in (f"{provider.label} MCP", "/".join(cleaned.values()))
                if part
            ),
            "settings": cleaned,
        },
    )
    tools = discover_tools(connection)
    connection.available_tools = sorted(tools)
    connection.metadata["tools"] = tools
    connection.full_clean()
    try:
        with transaction.atomic():
            connection.save()
    except IntegrityError:
        raise ConnectionConflict("This binding already has a connection.") from None
    return connection


def update_mcp_connection(
    connection: Connection,
    display_name: str | None,
    token: str | None,
    settings: dict[str, str] | None,
) -> Connection:
    """Replace the token or scope, then rediscover tools before saving anything.

    A blank token keeps the current one, unless the connection was revoked.
    """
    if connection.kind != Connection.Kind.MCP:
        raise ConnectionConflict("Only MCP connections can be edited here.")
    provider = mcp_provider(connection.provider)
    if token:
        if not re.fullmatch(provider.token_pattern, token):
            raise MCPError(provider.token_hint)
        connection.access_token = token
    elif connection.status == Connection.Status.REVOKED or not connection.access_token:
        raise MCPError("Enter a new token to reconnect.")
    cleaned = provider.clean_settings(
        connection_settings(connection) if settings is None else settings
    )
    if display_name is not None:
        connection.display_name = display_name.strip() or provider.label
    connection.endpoint_url = provider.endpoint(cleaned)
    connection.status = Connection.Status.CONNECTED
    connection.metadata = {
        **connection.metadata,
        "target": " · ".join(
            part
            for part in (f"{provider.label} MCP", "/".join(cleaned.values()))
            if part
        ),
        "settings": cleaned,
    }
    # Raises before saving, so a bad token or scope leaves the stored one alone.
    tools = discover_tools(connection)
    connection.available_tools = sorted(tools)
    connection.metadata["tools"] = tools
    connection.full_clean()
    connection.save()
    return connection
