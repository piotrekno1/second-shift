"""Populate the registry through explicit imports in stable order.

Import order sets the tools/list order. Add each domain's module here.
"""

from secondshift.mcp_server.tools_registry import registry

registered_tool_domains: tuple[object, ...] = ()

__all__ = ("registry",)
