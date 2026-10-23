"""The first supported MCP connection: GitHub HTTP, bearer token, read only."""

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

from secondshift.config.constants import (
    GITHUB_MCP_TOOLS,
    GITHUB_MCP_URL,
    GITHUB_REPOSITORY_PATTERN,
    GitHubMCPTool,
)
from secondshift.core.models import Workspace
from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import PinnedTransport
from secondshift.integrations.services import ConnectionConflict

MCP_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
MCP_MAX_PAGES = 10
MCP_SCHEMA_MAX_DEPTH = 30


class GitHubMCPError(ValueError):
    pass


class GitHubMCPRequestError(GitHubMCPError):
    """A remote failure the agent may report as unavailable evidence."""


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream) -> None:
        self.inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.inner:
            size += len(chunk)
            if size > MCP_RESPONSE_MAX_BYTES:
                raise GitHubMCPError("The MCP response exceeded the size limit.")
            yield chunk

    async def aclose(self) -> None:
        await self.inner.aclose()


class MCPTransport(PinnedTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise GitHubMCPError("Compressed MCP responses are not supported.")
        response.stream = BoundedStream(response.stream)
        return response


def validate_mcp_connection(connection: Connection) -> None:
    if (
        connection.kind != Connection.Kind.MCP
        or connection.provider != Connection.Provider.GITHUB
        or connection.endpoint_url != GITHUB_MCP_URL
        or connection.auth_strategy != Connection.AuthStrategy.API_KEY
        or connection.status != Connection.Status.CONNECTED
        or not connection.access_token
        or (connection.expires_at and connection.expires_at <= timezone.now())
    ):
        raise GitHubMCPError("This GitHub MCP connection is unavailable.")


async def github_mcp_request(
    connection: Connection,
    tool: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """No token, HTTP exception text, or remote error text leaves this boundary."""
    validate_mcp_connection(connection)
    try:
        with anyio.fail_after(settings.OUTBOUND_TIMEOUT_SECONDS):
            async with (
                httpx.AsyncClient(
                    transport=MCPTransport(),
                    headers={
                        "Authorization": f"Bearer {connection.access_token}",
                        "X-MCP-Tools": ",".join(GITHUB_MCP_TOOLS),
                        "X-MCP-Readonly": "true",
                        "Accept-Encoding": "identity",
                    },
                    timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                streamable_http_client(GITHUB_MCP_URL, http_client=client) as (
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
                        raise GitHubMCPError(
                            "GitHub could not complete this tool call."
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
                            if item.name in GITHUB_MCP_TOOLS:
                                tools[item.name] = {
                                    "description": item.description or item.name,
                                    "inputSchema": item.inputSchema,
                                }
                        cursor = page.nextCursor
                        if not cursor:
                            break
                    else:
                        raise GitHubMCPError(
                            "MCP tool discovery exceeded the page limit."
                        )
                    if not tools:
                        raise GitHubMCPError(
                            "GitHub exposed no supported briefing tools."
                        )
                    payload = tools
    except Exception:
        # SDK exception groups can embed request headers or remote response text.
        raise GitHubMCPRequestError(
            "GitHub MCP request failed. Check the token, permissions, and service availability."
        ) from None
    encoded = json.dumps(payload)
    if len(encoded.encode()) > MCP_RESPONSE_MAX_BYTES:
        raise GitHubMCPError("The MCP result exceeded the size limit.")
    return json.loads(encoded.replace(connection.access_token, "[redacted]"))


def validate_tool_schema(value: Any, depth: int = 0) -> None:
    if depth > MCP_SCHEMA_MAX_DEPTH:
        raise GitHubMCPError("The MCP schema is too deeply nested.")
    if isinstance(value, dict):
        if any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")):
            raise GitHubMCPError("MCP schema references are not supported.")
        for item in value.values():
            validate_tool_schema(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_tool_schema(item, depth + 1)


def discover_github_tools(connection: Connection) -> dict[str, Any]:
    tools = async_to_sync(github_mcp_request)(connection)
    for tool in tools.values():
        schema = tool["inputSchema"]
        validate_tool_schema(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:
            raise GitHubMCPError("GitHub returned an invalid tool schema.") from None
        if schema.get("type") != "object":
            raise GitHubMCPError("MCP tool arguments must be an object.")
    return tools


def create_github_mcp_connection(
    workspace: Workspace,
    binding_key: str,
    display_name: str,
    token: str,
) -> Connection:
    if not re.fullmatch(r"[A-Za-z0-9_]+", token):
        raise GitHubMCPError("Enter a GitHub personal access token without whitespace.")
    if Connection.objects.filter(workspace=workspace, binding_key=binding_key).exists():
        raise ConnectionConflict("This binding already has a connection.")
    connection = Connection(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key=binding_key,
        display_name=display_name.strip() or "GitHub",
        endpoint_url=GITHUB_MCP_URL,
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token=token,
        metadata={"target": "GitHub MCP"},
    )
    tools = discover_github_tools(connection)
    connection.available_tools = sorted(tools)
    connection.metadata["tools"] = tools
    connection.full_clean()
    try:
        with transaction.atomic():
            connection.save()
    except IntegrityError:
        raise ConnectionConflict("This binding already has a connection.") from None
    return connection


def scoped_github_arguments(
    repository: str, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Enforce the owner's repository scope independently of model instructions."""
    if tool not in GITHUB_MCP_TOOLS:
        raise GitHubMCPError("This GitHub tool is not supported.")
    if not re.fullmatch(GITHUB_REPOSITORY_PATTERN, repository):
        raise GitHubMCPError("The job has no valid GitHub repository scope.")
    owner, repo = repository.split("/", 1)
    scoped = dict(arguments)
    for key, value in (("owner", owner), ("repo", repo)):
        if key in scoped and scoped[key] != value:
            raise GitHubMCPError("The tool must target the approved repository.")
        scoped[key] = value
    if tool == GitHubMCPTool.LIST_COMMITS and scoped.get("sha"):
        raise GitHubMCPError("List commits from the default branch by omitting sha.")
    if tool == GitHubMCPTool.SEARCH_PULL_REQUESTS:
        query = str(scoped.get("query", ""))
        # OR can widen a repo-qualified search. Disallow it rather than attempt
        # to rewrite the provider's query language.
        if re.search(r"\bOR\b", query, re.IGNORECASE):
            raise GitHubMCPError("Use separate searches instead of OR.")
        query = re.sub(rf"(?i)(?<!\S)repo:{re.escape(repository)}(?=\s|$)", "", query)
        if re.search(r"repo\s*:", query, re.IGNORECASE):
            raise GitHubMCPError("The search must target the approved repository.")
        scoped["query"] = f"repo:{repository} {query.strip()}".strip()
    return scoped
