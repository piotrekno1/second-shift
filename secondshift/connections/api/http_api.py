"""HTTP API connections described by an OpenAPI spec."""

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.api.shared import apply_approval, owned_connection
from secondshift.connections.http_api.connections import (
    create_api_connection,
    summarize,
    update_api_connection,
)
from secondshift.connections.http_api.spec import APIError
from secondshift.connections.schemas import (
    APIConnectionCreateSchema,
    APIConnectionUpdateSchema,
    APISpecSchema,
    APISpecSummarySchema,
    ConnectionSchema,
)
from secondshift.connections.services import ConnectionConflict
from secondshift.workspaces.access import Action, require

router = Router(auth=django_auth, tags=["connections"])


@router.post(
    "/connections/api/inspect", response=APISpecSummarySchema, auth=django_auth
)
def inspect_api_spec(
    request: HttpRequest, payload: APISpecSchema
) -> APISpecSummarySchema:
    """The spec's title, servers, and operation counts, before connecting."""
    require(request, Action.ADD_API_CONNECTIONS)
    try:
        summary = summarize(payload.spec_url.strip(), payload.spec_text)
    except APIError as error:
        raise HttpError(422, str(error)) from error
    return APISpecSummarySchema(
        title=summary.title,
        servers=list(summary.servers),
        operations=summary.operations,
        read_only=summary.read_only,
    )


@router.post("/connections/api", response=ConnectionSchema, auth=django_auth)
def create_api(
    request: HttpRequest, payload: APIConnectionCreateSchema
) -> ConnectionSchema:
    workspace = require(request, Action.ADD_API_CONNECTIONS)
    try:
        connection = create_api_connection(
            workspace,
            payload.binding_key,
            payload.display_name,
            payload.spec_url.strip(),
            payload.spec_text,
            payload.base_url,
            payload.auth,
            payload.header.strip(),
            payload.token,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except APIError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, "The API connection configuration is invalid.") from error
    return apply_approval(connection, payload.require_approval)


@router.post(
    "/connections/{connection_id}/api", response=ConnectionSchema, auth=django_auth
)
def update_api(
    request: HttpRequest, connection_id: int, payload: APIConnectionUpdateSchema
) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, Action.EDIT_CONNECTIONS)
    try:
        connection = update_api_connection(
            connection,
            payload.display_name,
            payload.token,
            payload.base_url,
            payload.operations,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except APIError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, "The API connection configuration is invalid.") from error
    return apply_approval(connection, payload.require_approval)
