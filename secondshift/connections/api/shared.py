"""Helpers the connection routes share."""

from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from secondshift.connections.dao import workspace_connections
from secondshift.connections.models import Connection
from secondshift.connections.schemas import ConnectionSchema
from secondshift.connections.serialization import serialize_connection
from secondshift.jobs.automatic_grants import approve_waiting_for, set_require_approval
from secondshift.workspaces.access import Action, require


def apply_approval(
    connection: Connection, require_approval: bool | None
) -> ConnectionSchema:
    """Save the owner's approval choice and approve requests it now allows."""
    if require_approval is None:
        approve_waiting_for(connection)
    else:
        set_require_approval(connection, require_approval)
    return serialize_connection(connection)


def owned_connection(
    request: HttpRequest, connection_id: int, action: Action
) -> Connection:
    workspace = require(request, action)
    return get_object_or_404(workspace_connections(workspace.pk), pk=connection_id)
