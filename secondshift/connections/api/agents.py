"""Agent subscription logins. Only the login's owner changes one."""

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.agents import (
    AgentConnectionError,
    check_agent_connection,
    disconnect_agent_connection,
    reconnect_agent_connection,
    save_agent_connection,
)
from secondshift.connections.dao import (
    own_agent_connections,
    workspace_agent_connections,
)
from secondshift.connections.execution import use_when_nothing_runs
from secondshift.connections.models import AgentConnection
from secondshift.connections.schemas import (
    AgentConnectionInput,
    AgentConnectionSchema,
    AgentRunLimitInput,
)
from secondshift.connections.serialization import with_recent_runs
from secondshift.workspaces.access import Action, current_workspace, require

router = Router(auth=django_auth, tags=["connections"])


@router.get("/agent-connections", response=list[AgentConnectionSchema])
def agent_connections(request: HttpRequest) -> list[AgentConnection]:
    workspace = current_workspace(request)
    if not workspace:
        return []
    return with_recent_runs(workspace_agent_connections(workspace), workspace.pk)


@router.post("/agent-connections", response=AgentConnectionSchema)
def connect_agent(
    request: HttpRequest, payload: AgentConnectionInput
) -> AgentConnection:
    workspace = require(request, Action.ADD_CONNECTIONS)
    try:
        connection = save_agent_connection(
            workspace,
            request.user,
            payload.backend,
            payload.display_name,
            payload.auth.get_secret_value(),
        )
    except AgentConnectionError as error:
        raise HttpError(422, str(error)) from None
    use_when_nothing_runs(workspace, login=connection)
    return connection


@router.post("/agent-connections/{connection_id}", response=AgentConnectionSchema)
def reconnect_agent(
    request: HttpRequest, connection_id: int, payload: AgentConnectionInput
) -> AgentConnection:
    workspace = require(request, Action.EDIT_CONNECTIONS)
    connection = get_object_or_404(
        own_agent_connections(workspace, request.user), pk=connection_id
    )
    try:
        return reconnect_agent_connection(
            connection,
            request.user,
            payload.backend,
            payload.display_name,
            payload.auth.get_secret_value(),
        )
    except AgentConnectionError as error:
        raise HttpError(422, str(error)) from None


@router.post("/agent-connections/{connection_id}/check", response=AgentConnectionSchema)
def check_agent(request: HttpRequest, connection_id: int) -> AgentConnection:
    """Only the login's owner checks it. The check uses their plan's allowance."""
    workspace = require(request, Action.TEST_CONNECTIONS)
    connection = get_object_or_404(
        own_agent_connections(workspace, request.user), pk=connection_id
    )
    try:
        check_agent_connection(connection)
    except AgentConnectionError as error:
        raise HttpError(409, str(error)) from None
    return connection


@router.post(
    "/agent-connections/{connection_id}/run-limit", response=AgentConnectionSchema
)
def set_agent_run_limit(
    request: HttpRequest, connection_id: int, payload: AgentRunLimitInput
) -> AgentConnection:
    """Only the login's owner sets its cap. It is their subscription."""
    workspace = require(request, Action.EDIT_CONNECTIONS)
    connection = get_object_or_404(
        own_agent_connections(workspace, request.user), pk=connection_id
    )
    connection.max_runs_per_day = payload.max_runs_per_day
    connection.save(update_fields=["max_runs_per_day", "updated_at"])
    return connection


@router.delete("/agent-connections/{connection_id}", response={204: None})
def disconnect_agent(request: HttpRequest, connection_id: int) -> Status[None]:
    workspace = require(request, Action.DISCONNECT_CONNECTIONS)
    connection = get_object_or_404(
        own_agent_connections(workspace, request.user), pk=connection_id
    )
    # A cached copy the runner keeps is removed by the worker.
    disconnect_agent_connection(connection)
    return Status(204, None)
