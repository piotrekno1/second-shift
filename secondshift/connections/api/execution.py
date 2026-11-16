"""The workspace's default way to run jobs."""

from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.execution import (
    execution_configuration,
    save_execution_preference,
)
from secondshift.connections.schemas import (
    ExecutionConfigurationSchema,
    ExecutionPreferenceSchema,
)
from secondshift.workspaces.access import Action, current_workspace, require

router = Router(auth=django_auth, tags=["connections"])


@router.get("/settings/agent", response=ExecutionConfigurationSchema)
def default_agent(request: HttpRequest) -> dict[str, Any]:
    workspace = current_workspace(request)
    if workspace is None:
        raise HttpError(404, "Choose a workspace first.")
    return execution_configuration(workspace, request.user)


@router.post("/settings/agent", response=ExecutionConfigurationSchema)
def set_default_agent(
    request: HttpRequest, payload: ExecutionPreferenceSchema
) -> dict[str, Any]:
    workspace = require(request, Action.REVIEW_CAPABILITIES)
    try:
        save_execution_preference(workspace, payload.key, request.user)
    except ValidationError as error:
        raise HttpError(422, error.messages[0]) from None
    return execution_configuration(workspace, request.user)
