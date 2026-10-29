from django.db import transaction

from secondshift.connections.bindings import bind, ensure_binding_free
from secondshift.connections.errors import ConnectionConflict
from secondshift.connections.models import Connection
from secondshift.connections.webhooks import validate_webhook_url, webhook_target_label
from secondshift.workspaces.models import Workspace


def connection_target(connection: Connection) -> str:
    if connection.provider == Connection.Provider.WEBHOOK or (
        connection.kind in {Connection.Kind.MODEL, Connection.Kind.MCP}
    ):
        return str(connection.metadata.get("target", ""))
    return ""


def create_webhook_connection(
    workspace: Workspace,
    binding_key: str,
    display_name: str,
    url: str,
) -> Connection:
    url = validate_webhook_url(url)
    ensure_binding_free(workspace.pk, binding_key)
    connection = Connection(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        display_name=display_name.strip() or binding_key,
        auth_strategy=Connection.AuthStrategy.NONE,
        delivery_url=url,
        metadata={"target": webhook_target_label(url)},
    )
    connection.full_clean()
    with transaction.atomic():
        connection.save()
        bind(connection, binding_key)
    return connection


def update_webhook_connection(
    connection: Connection,
    display_name: str | None,
    url: str | None,
) -> Connection:
    if connection.provider != Connection.Provider.WEBHOOK:
        raise ConnectionConflict("Only webhook connections can be edited here.")
    if display_name is not None:
        connection.display_name = display_name.strip() or connection.display_name
    if url:
        url = validate_webhook_url(url)
        connection.delivery_url = url
        connection.metadata = {
            **connection.metadata,
            "target": webhook_target_label(url),
        }
        connection.status = Connection.Status.CONNECTED
    elif connection.status == Connection.Status.REVOKED:
        raise ConnectionConflict("Enter a new URL to reconnect this webhook.")
    connection.full_clean()
    connection.save()
    return connection


def revoke_connection(connection: Connection) -> Connection:
    """Stop all use immediately and discard stored credentials; keep the record."""
    connection.status = Connection.Status.REVOKED
    connection.is_default = False
    connection.delivery_url = ""
    connection.access_token = ""
    connection.refresh_token = ""
    connection.metadata = {
        key: value for key, value in connection.metadata.items() if key != "target"
    }
    connection.save(
        update_fields=[
            "status",
            "delivery_url",
            "access_token",
            "refresh_token",
            "metadata",
            "is_default",
            "updated_at",
        ]
    )
    return connection


@transaction.atomic
def delete_connection(connection: Connection) -> None:
    if connection.kind == Connection.Kind.IDENTITY:
        raise ConnectionConflict("Sign-in connections are managed by their provider.")
    if connection.kind == Connection.Kind.SOURCE and connection.repositories.exists():
        raise ConnectionConflict(
            "This installation has repository history. Disconnect it instead."
        )
    # Grants are review records: they return to pending instead of disappearing,
    # so the jobs show up again under "Needed by your jobs".
    connection.job_grants.update(
        connection=None,
        status="pending",
        allowed_tools=[],
        reviewed_by=None,
        reviewed_at=None,
    )
    connection.delete()
