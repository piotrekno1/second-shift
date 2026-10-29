from importlib.metadata import version

from thin_mcp import McpServer

from secondshift.mcp_server.tools import registry

server = McpServer(
    name="secondshift",
    version=version("secondshift"),
    protocol_versions=("2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"),
    tools=registry,
)
