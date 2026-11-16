"""Model provider connections and their model lists."""

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.api.shared import owned_connection
from secondshift.connections.credential_store import Secret, read_secret
from secondshift.connections.execution import apply_model_default
from secondshift.connections.model_providers import (
    CheckFailed,
    ModelProviderError,
    checked_api_key,
    create_model_connection,
    fetch_models,
    update_model_connection,
    validated_endpoint,
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
    require(request, Action.ADD_MODEL_PROVIDERS)
    try:
        connection = Connection(
            provider=payload.provider,
            endpoint_url=validated_endpoint(payload.provider, payload.base_url),
            access_token=checked_api_key(payload.provider, payload.api_key),
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
        preview = Connection(
            provider=connection.provider,
            endpoint_url=validated_endpoint(
                connection.provider,
                payload.base_url
                if payload.base_url is not None
                else connection.endpoint_url,
            ),
            access_token=payload.api_key.strip()
            if payload.api_key and payload.api_key.strip()
            else read_secret(connection, Secret.TOKEN),
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
        )
    except ModelProviderError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    apply_model_default(connection, payload.is_default)
    return serialize_connection(connection)
