"""Model provider connections and their model lists."""

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.api.shared import owned_connection
from secondshift.connections.execution import apply_model_default
from secondshift.connections.model_providers import (
    CheckFailed,
    ModelProviderError,
    create_model_connection,
    fetch_models,
    preview_changed_connection,
    preview_new_connection,
    update_model_connection,
)
from secondshift.connections.models import Connection
from secondshift.connections.schemas import (
    ConnectionSchema,
    ModelConnectionCreateSchema,
    ModelConnectionUpdateSchema,
)
from secondshift.connections.serialization import serialize_connection
from secondshift.workspaces.access import Action, require

router = Router(auth=django_auth, tags=["connections"])


@router.post("/connections/model/models", response=list[str], auth=django_auth)
def discover_model_provider_models(
    request: HttpRequest, payload: ModelConnectionCreateSchema
) -> list[str]:
    workspace = require(request, Action.ADD_MODEL_PROVIDERS)
    try:
        connection = preview_new_connection(
            workspace,
            payload.provider,
            payload.base_url,
            payload.api_key,
            payload.credential_reference,
        )
        return fetch_models(connection)
    except (ModelProviderError, CheckFailed) as error:
        raise HttpError(422, str(error)) from error


@router.post(
    "/connections/{connection_id}/model/models", response=list[str], auth=django_auth
)
def refresh_model_provider_models(
    request: HttpRequest, connection_id: int, payload: ModelConnectionUpdateSchema
) -> list[str]:
    connection = owned_connection(request, connection_id, Action.EDIT_MODEL_PROVIDERS)
    if connection.kind != Connection.Kind.MODEL:
        raise HttpError(422, "This is not a model provider connection.")
    try:
        # Preview changes without saving credentials, settings, or discovery results.
        preview = preview_changed_connection(
            connection,
            payload.base_url,
            payload.api_key,
            payload.credential_reference,
        )
        return fetch_models(preview)
    except (ModelProviderError, CheckFailed) as error:
        raise HttpError(422, str(error)) from error


@router.post("/connections/model", response=ConnectionSchema, auth=django_auth)
def create_model_provider(
    request: HttpRequest, payload: ModelConnectionCreateSchema
) -> ConnectionSchema:
    workspace = require(request, Action.ADD_MODEL_PROVIDERS)
    try:
        connection = create_model_connection(
            workspace,
            payload.provider,
            payload.display_name,
            payload.api_key,
            endpoint_url=payload.base_url,
            allowed_models=payload.allowed_models,
            default_model=payload.default_model,
            credential_reference=payload.credential_reference,
        )
    except ModelProviderError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    apply_model_default(connection, payload.is_default)
    return serialize_connection(connection)


@router.post(
    "/connections/{connection_id}/model", response=ConnectionSchema, auth=django_auth
)
def update_model_provider(
    request: HttpRequest, connection_id: int, payload: ModelConnectionUpdateSchema
) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, Action.EDIT_MODEL_PROVIDERS)
    try:
        connection = update_model_connection(
            connection,
            display_name=payload.display_name,
            api_key=payload.api_key,
            endpoint_url=payload.base_url,
            allowed_models=payload.allowed_models,
            default_model=payload.default_model,
            credential_reference=payload.credential_reference,
        )
    except ModelProviderError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    apply_model_default(connection, payload.is_default)
    return serialize_connection(connection)
