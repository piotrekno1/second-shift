from secondshift.connections.models import Connection
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
            "base_url": connection.metadata.get("base_url", ""),
            "spec_url": connection.metadata.get("spec_url", ""),
        }
    return {}


def serialize_connection(
    connection: Connection, usage: ConnectionUsage | None = None
) -> ConnectionSchema:
    return ConnectionSchema(
        id=connection.id,
        provider=connection.provider,
        kind=connection.kind,
        binding_key=connection.binding_key,
        display_name=connection.display_name,
        status=connection.status,
        expires_at=connection.expires_at,
        available_tools=connection.available_tools,
        target=connection_target(connection),
        allowed_models=connection.allowed_models,
        default_model=connection.default_model,
        is_default=connection.is_default,
        available_models=list(connection.metadata.get("available_models", [])),
        last_check=connection.metadata.get("last_check"),
        settings=connection_settings_view(connection),
        auth=(
            (
                "oauth"
                if connection.auth_strategy == Connection.AuthStrategy.OAUTH
                else "token"
            )
            if connection.kind in {Connection.Kind.MCP, Connection.Kind.API}
            else ""
        ),
        scopes=list(connection.scopes)
        if connection.kind == Connection.Kind.MCP
        else [],
        offered_tools=[
            OfferedToolSchema(
                name=name,
                description=str(tool.get("description", ""))[:500],
                read_only=bool(tool.get("readOnly")),
            )
            for name, tool in sorted(
                (connection.metadata.get("offered_tools") or {}).items()
            )
        ],
        usage=None
        if usage is None
        else ConnectionUsageSchema(
            **{**vars(usage), "spend_7d_usd": float(usage.spend_7d_usd)}
        ),
    )
