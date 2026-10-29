"""What a manifest can ask for in a workspace: bindings and their tools.

Only names, descriptions, input schemas and whether a tool may change data
are listed. Endpoints, paths, auth details and credentials never are. Tools
the owner has not allowed are counted but not named.
"""

from dataclasses import dataclass, field
from typing import Any

from secondshift.connections.bindings import BINDING_KINDS
from secondshift.connections.external import changing_tools
from secondshift.connections.mcp.github import GITHUB_MCP
from secondshift.connections.mcp.sentry import SENTRY_MCP
from secondshift.connections.models import Connection, ConnectionBinding
from secondshift.workspaces.models import Workspace

# Descriptions come from third-party servers. Long ones are cut.
DESCRIPTION_MAX = 500
BUILT_IN_PROVIDERS = (GITHUB_MCP, SENTRY_MCP)


@dataclass(frozen=True)
class CatalogTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    may_change_data: bool


@dataclass(frozen=True)
class CatalogBinding:
    binding: str
    kind: str
    provider: str
    connected: bool
    tools: list[CatalogTool] = field(default_factory=list)
    # Tools the service offers that the owner has not allowed.
    hidden_tools: int = 0


@dataclass(frozen=True)
class BuiltInProvider:
    provider: str
    kind: str
    tools: list[str]
    # Bindings in this workspace that already connect it.
    bindings: list[str]


def catalog_tool(name: str, definition: dict[str, Any], changing: bool) -> CatalogTool:
    return CatalogTool(
        name=name,
        description=str(definition.get("description", ""))[:DESCRIPTION_MAX],
        input_schema=dict(definition.get("inputSchema") or {}),
        may_change_data=changing,
    )


def catalog_binding(key: str, connection: Connection) -> CatalogBinding:
    definitions = connection.metadata.get("tools") or {}
    offered = connection.metadata.get("offered_tools") or {}
    allowed = sorted(connection.available_tools)
    changing = set(changing_tools(connection, allowed))
    return CatalogBinding(
        binding=key,
        kind=connection.kind,
        provider=connection.get_provider_display(),
        connected=connection.status == Connection.Status.CONNECTED,
        tools=[
            catalog_tool(name, definitions.get(name) or {}, name in changing)
            for name in allowed
        ],
        hidden_tools=len(set(offered) - set(allowed)),
    )


def workspace_catalog(
    workspace: Workspace,
) -> tuple[list[CatalogBinding], list[BuiltInProvider]]:
    bindings = list(
        ConnectionBinding.objects.filter(
            workspace=workspace, connection__kind__in=BINDING_KINDS
        )
        .select_related("connection")
        .order_by("key")
    )
    built_ins = [
        BuiltInProvider(
            provider=provider.label,
            kind=Connection.Kind.MCP,
            tools=list(provider.tools),
            bindings=[
                binding.key
                for binding in bindings
                if binding.connection.provider == provider.key
            ],
        )
        for provider in BUILT_IN_PROVIDERS
    ]
    return [
        catalog_binding(binding.key, binding.connection) for binding in bindings
    ], built_ins
