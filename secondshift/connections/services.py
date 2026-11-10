from django.db import IntegrityError, transaction

from secondshift.connections.dao import binding_taken
from secondshift.connections.models import Connection
from secondshift.connections.outbound import host_label
from secondshift.connections.webhooks import validate_webhook_url
from secondshift.workspaces.models import Workspace


class ConnectionConflict(ValueError):
    pass


def connection_target(connection: Connection) -> str:
    """The host a connection reaches, as saved when it was set up."""
    return str(connection.metadata.get("target", ""))


def create_webhook_connection(
    workspace: Workspace,
    binding_key: str,
    display_name: str,
    url: str,
) -> Connection:
    url = validate_webhook_url(url)
    if binding_taken(workspace.pk, binding_key):
        raise ConnectionConflict(
            f"A connection for {binding_key} already exists in this workspace."
        )
    connection = Connection(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        binding_key=binding_key,
        display_name=display_name.strip() or binding_key,
        auth_strategy=Connection.AuthStrategy.NONE,
        delivery_url=url,
        metadata={"target": host_label(url)},
    )
    connection.full_clean()
    try:
        with transaction.atomic():
            connection.save()
    except IntegrityError as error:
        raise ConnectionConflict(
            f"A connection for {binding_key} already exists in this workspace."
        ) from error
    return connection


def update_webhook_connection(
    connection: Connection,
    display_name: str | None,
    url: str | None,
) -> Connection:
    if connection.provider != Connection.Provider.WEBHOOK:
        raise ConnectionConflict("Only webhook connections can be edited here.")
    if display_name is not None:
        connection.display_name = display_name.strip() or connection.binding_key
    if url:
        url = validate_webhook_url(url)
        connection.delivery_url = url
        connection.metadata = {
            **connection.metadata,
            "target": host_label(url),
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
            "updated_at",
        ]
    )
    return connection
