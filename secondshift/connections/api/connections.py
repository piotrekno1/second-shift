"""Routes every connection kind shares: list, revoke, delete and test."""

from django.http import HttpRequest
from ninja import Router, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.api.shared import owned_connection
from secondshift.connections.dao import workspace_connections
from secondshift.connections.http_api.connections import recheck_api_connection
from secondshift.connections.http_api.spec import APIError
from secondshift.connections.mcp.base import MCPError
from secondshift.connections.mcp.client import recheck_mcp_connection
from secondshift.connections.model_providers import check_model_connection
from secondshift.connections.models import Connection
from secondshift.connections.schemas import ConnectionSchema, ConnectionTestSchema
from secondshift.connections.serialization import serialize_connection
from secondshift.connections.services import revoke_connection
from secondshift.connections.webhooks import connection_test_payload, deliver_webhook
from secondshift.jobs.grants import delete_connection
from secondshift.repositories.github.check import check_source
from secondshift.runs.reporting.connection_usage import (
    ConnectionUsage,
    connection_usage,
)
from secondshift.workspaces.access import Action, current_workspace

router = Router(auth=django_auth, tags=["connections"])


@router.get("/connections", response=list[ConnectionSchema], auth=django_auth)
def connections(request: HttpRequest) -> list[ConnectionSchema]:
    workspace = current_workspace(request)
    if not workspace:
        return []
    usage = connection_usage(workspace)
    return [
        serialize_connection(item, usage.get(item.pk, ConnectionUsage()))
        for item in workspace_connections(workspace.pk)
    ]


@router.post(
    "/connections/{connection_id}/revoke", response=ConnectionSchema, auth=django_auth
)
def revoke(request: HttpRequest, connection_id: int) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, Action.DISCONNECT_CONNECTIONS)
    return serialize_connection(revoke_connection(connection))


@router.delete("/connections/{connection_id}", response={204: None}, auth=django_auth)
def remove_connection(request: HttpRequest, connection_id: int) -> Status[None]:
    connection = owned_connection(request, connection_id, Action.DELETE_CONNECTIONS)
    delete_connection(connection)
    return Status(204, None)


@router.post(
    "/connections/{connection_id}/test", response=ConnectionTestSchema, auth=django_auth
)
def test_connection(request: HttpRequest, connection_id: int) -> ConnectionTestSchema:
    connection = owned_connection(request, connection_id, Action.TEST_CONNECTIONS)
    if connection.kind == Connection.Kind.MCP:
        try:
            message = recheck_mcp_connection(connection)
        except MCPError as error:
            return ConnectionTestSchema(ok=False, status_code=None, message=str(error))
        return ConnectionTestSchema(ok=True, status_code=None, message=message)
    if connection.kind == Connection.Kind.API:
        try:
            message = recheck_api_connection(connection)
        except APIError as error:
            return ConnectionTestSchema(ok=False, status_code=None, message=str(error))
        return ConnectionTestSchema(ok=True, status_code=None, message=message)
    if connection.kind == Connection.Kind.MODEL:
        check = check_model_connection(connection)
        return ConnectionTestSchema(
            ok=check.ok, status_code=None, message=check.message, models=check.models
        )
    if (
        connection.kind == Connection.Kind.SOURCE
        and connection.provider == Connection.Provider.GITHUB
    ):
        check = check_source(connection)
        return ConnectionTestSchema(
            ok=check.ok, status_code=None, message=check.message
        )
    if connection.provider != Connection.Provider.WEBHOOK:
        raise HttpError(400, "Testing is not available for this connection yet.")
    delivery = deliver_webhook(connection, connection_test_payload(connection))
    return ConnectionTestSchema(
        ok=delivery.ok, status_code=delivery.status_code, message=delivery.message
    )
