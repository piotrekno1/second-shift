"""Populate the registry through explicit imports in stable order.

Import order sets the tools/list order. Add each domain's module here.
"""

from secondshift.mcp_server.tools import jobs, runs, repositories, manifests, tasks  # noqa: F401, I001
from secondshift.mcp_server.tools_registry import registry

__all__ = ("registry",)
