import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from secondshift.connections.mcp.base import MCPError
from secondshift.connections.mcp.client import (
    MCP_MAX_OFFERED_TOOLS,
    apply_credentials,
    offered_tool,
    offered_tools,
)
from secondshift.connections.mcp.custom import CUSTOM_MCP
from secondshift.connections.mcp.github import GITHUB_MCP
from secondshift.connections.models import Connection


def tool(name: str, read_only: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} tool",
        inputSchema={"type": "object"},
        annotations=SimpleNamespace(readOnlyHint=read_only),
    )


class Session:
    """Serves tools in pages. A page with a cursor says another one follows."""

    def __init__(self, pages: list[list[SimpleNamespace]], endless: bool = False):
        self.pages = pages
        self.endless = endless

    async def list_tools(self, cursor: str | None = None) -> SimpleNamespace:
        index = int(cursor or 0)
        has_more = self.endless or index + 1 < len(self.pages)
        return SimpleNamespace(
            tools=self.pages[index % len(self.pages)],
            nextCursor=str(index + 1) if has_more else None,
        )


def offered(session: Session, provider: Any = CUSTOM_MCP) -> dict[str, Any]:
    return asyncio.run(offered_tools(session, provider))  # type: ignore[arg-type]


def test_a_fixed_provider_offers_only_its_own_tools() -> None:
    allowed = next(iter(GITHUB_MCP.tools))

    assert offered_tool(tool(allowed), GITHUB_MCP) is not None
    assert offered_tool(tool("delete_repository"), GITHUB_MCP) is None
    # A custom server's owner chooses, so every tool is offered.
    assert offered_tool(tool("delete_repository", False), CUSTOM_MCP) == {
        "description": "delete_repository tool",
        "inputSchema": {"type": "object"},
        "readOnly": False,
    }


def test_a_provider_can_require_read_only_tools() -> None:
    strict = replace(CUSTOM_MCP, require_read_only_hint=True)

    assert offered_tool(tool("read", True), strict) is not None
    assert offered_tool(tool("write", False), strict) is None


def test_tools_are_collected_across_pages() -> None:
    tools = offered(Session([[tool("a")], [tool("b")]]))

    assert set(tools) == {"a", "b"}


@pytest.mark.parametrize(
    ("session", "message"),
    [
        (Session([[]]), "exposed no supported tools"),
        (Session([[tool("a")]], endless=True), "exceeded the page limit"),
        (
            Session([[tool(f"t{i}") for i in range(MCP_MAX_OFFERED_TOOLS + 1)]]),
            "offers too many tools",
        ),
    ],
)
def test_discovery_stops_at_its_limits(session: Session, message: str) -> None:
    with pytest.raises(MCPError, match=message):
        offered(session)


def test_a_signed_in_connection_changes_only_by_signing_in() -> None:
    connection = Connection(
        auth_strategy=Connection.AuthStrategy.OAUTH,
        status=Connection.Status.CONNECTED,
    )

    with pytest.raises(MCPError, match="Sign in again to change"):
        apply_credentials(connection, CUSTOM_MCP, "new-token", None, {})
    with pytest.raises(MCPError, match="Sign in again to change"):
        apply_credentials(connection, CUSTOM_MCP, None, {"url": "x"}, {"url": "y"})
    connection.status = Connection.Status.REVOKED
    with pytest.raises(MCPError, match="Sign in again to reconnect"):
        apply_credentials(connection, CUSTOM_MCP, None, None, {})


def test_a_revoked_token_connection_needs_a_new_token() -> None:
    connection = Connection(
        auth_strategy=Connection.AuthStrategy.API_KEY,
        status=Connection.Status.REVOKED,
        access_token="old",
    )

    with pytest.raises(MCPError, match="Enter a new token"):
        apply_credentials(connection, CUSTOM_MCP, None, None, {})
    apply_credentials(connection, CUSTOM_MCP, "fresh-token", None, {})
    assert connection.access_token == "fresh-token"
