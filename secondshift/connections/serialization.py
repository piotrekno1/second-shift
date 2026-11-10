from secondshift.connections.execution import recent_login_runs
from secondshift.connections.mcp.base import SOURCE_AUTH
from secondshift.connections.mcp.oauth import requested_scopes
from secondshift.connections.models import AgentConnection, Connection
from secondshift.connections.schemas import (
    ConnectionSchema,
    ConnectionUsageSchema,
    OfferedToolSchema,
)
from secondshift.connections.services import (
    connection_target,
)
from secondshift.runs.reporting.connection_usage import ConnectionUsage


def connection_settings_view(connection: Connection) -> dict[str, str]:
    """Owner-chosen settings that are safe to show. Never a credential."""
    if connection.kind == Connection.Kind.MCP:
        return dict(connection.metadata.get("settings", {}))
    if connection.kind == Connection.Kind.API:
        return {
            **connection.metadata.get("settings", {}),
            "base_url": connection.endpoint_url,
            "spec_url": connection.metadata.get("spec_url", ""),
        }
    return {}


def connection_auth(connection: Connection) -> str:
    """How the connection signs in: oauth, token, app, or empty."""
    if connection.kind == Connection.Kind.SOURCE:
        return (
            "app"
            if connection.auth_strategy == Connection.AuthStrategy.MANAGED
            else "token"
        )
    if connection.kind not in {Connection.Kind.MCP, Connection.Kind.API}:
        return ""
    if connection.auth_strategy == Connection.AuthStrategy.OAUTH:
        return "oauth"
    if connection.auth_strategy == Connection.AuthStrategy.MANAGED:
        return SOURCE_AUTH
    return "token"


def serialize_connection(
    connection: Connection, usage: ConnectionUsage | None = None
) -> ConnectionSchema:
    return ConnectionSchema(
        id=connection.id,
        provider=connection.provider,
        kind=connection.kind,
        binding_key=connection.binding_key,
        display_name=connection.display_name,
        status=connection.current_status,
        expires_at=connection.expires_at,
        available_tools=connection.available_tools,
        target=connection_target(connection),
        allowed_models=connection.allowed_models,
        default_model=connection.default_model,
        is_default=connection.kind == Connection.Kind.MODEL
        and connection.workspace.default_model_connection_id == connection.pk,
        available_models=list(connection.metadata.get("available_models", [])),
        last_check=connection.last_check,
        settings=connection_settings_view(connection),
        auth=connection_auth(connection),
        scopes=list(connection.scopes)
        if connection.kind == Connection.Kind.MCP
        else [],
        requested_scopes=requested_scopes(connection)
        if connection.kind == Connection.Kind.MCP
        and connection.auth_strategy == Connection.AuthStrategy.OAUTH
        else [],
        offered_tools=[
            OfferedToolSchema(
                name=name,
                description=str(tool.get("description", ""))[:500],
                read_only=bool(tool.get("readOnly")),
            )
            for name, tool in sorted(connection.offered_tools.items())
        ],
        require_approval=connection.require_approval,
        usage=None
        if usage is None
        else ConnectionUsageSchema(
            **{**vars(usage), "spend_7d_usd": float(usage.spend_7d_usd)}
        ),
    )


def with_recent_runs(
    connections: list[AgentConnection], workspace_id: int
) -> list[AgentConnection]:
    """Attach each login's recent run count for AgentConnectionSchema."""
    counts = recent_login_runs(workspace_id)
    for connection in connections:
        connection.recent_runs = counts[connection.pk]
    return connections
