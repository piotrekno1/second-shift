from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from ninja import Router, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.bindings import bind, unbind
from secondshift.connections.errors import ConnectionConflict
from secondshift.connections.http_api.connections import (
    create_api_connection,
    recheck_api_connection,
    summarize,
    update_api_connection,
)
from secondshift.connections.http_api.spec import APIError
from secondshift.connections.mcp.base import MCPError
from secondshift.connections.mcp.client import (
    create_mcp_connection,
    discover_tools,
    select_tools,
    update_mcp_connection,
)
from secondshift.connections.mcp.oauth import inspect_server
from secondshift.connections.mcp.providers import MCP_PROVIDERS, mcp_provider
from secondshift.connections.mcp.signin import (
    SignInError,
    complete_authorization,
    start_authorization,
)
from secondshift.connections.model_providers import (
    CheckFailed,
    ModelProviderError,
    check_model_connection,
    create_model_connection,
    fetch_models,
    update_model_connection,
    validated_endpoint,
)
from secondshift.connections.models import Connection, Secret
from secondshift.connections.schemas import (
    APIConnectionCreateSchema,
    APIConnectionUpdateSchema,
    APISpecSchema,
    APISpecSummarySchema,
    ConnectionBindingSchema,
    ConnectionSchema,
    ConnectionTestSchema,
    MCPConnectionCreateSchema,
    MCPConnectionUpdateSchema,
    MCPInspectResultSchema,
    MCPInspectSchema,
    MCPProviderSchema,
    MCPSettingSchema,
    MCPSignInSchema,
    MCPSignInStartSchema,
    ModelConnectionCreateSchema,
    ModelConnectionUpdateSchema,
    SecretSchema,
    WebhookConnectionCreateSchema,
    WebhookConnectionUpdateSchema,
)
from secondshift.connections.serialization import serialize_connection
from secondshift.connections.services import (
    create_webhook_connection,
    delete_connection,
    revoke_connection,
    update_webhook_connection,
)
from secondshift.connections.webhooks import (
    WebhookError,
    connection_test_payload,
    deliver_webhook,
)
from secondshift.runs.reporting.connection_usage import (
    ConnectionUsage,
    connection_usage,
)
from secondshift.workspaces.access import (
    Action,
    current_workspace,
    require,
)

router = Router(auth=django_auth, tags=["connections"])


@router.get("/connections", response=list[ConnectionSchema], auth=django_auth)
def connections(request: HttpRequest) -> list[ConnectionSchema]:
    workspace = current_workspace(request)
    if not workspace:
        return []
    usage = connection_usage(workspace)
    return [
        serialize_connection(item, usage.get(item.pk, ConnectionUsage()))
        for item in Connection.objects.filter(workspace=workspace).prefetch_related(
            "bindings"
        )
    ]


@router.get(
    "/connections/mcp-providers", response=list[MCPProviderSchema], auth=django_auth
)
def list_mcp_providers(request: HttpRequest) -> list[MCPProviderSchema]:
    """The built-in MCP servers an owner can connect. Never includes secrets."""
    return [
        MCPProviderSchema(
            key=provider.key,
            label=provider.label,
            description=provider.description,
            default_binding=provider.default_binding,
            token_label=provider.token_label,
            token_hint=provider.token_hint,
            setup_url=provider.setup_url,
            setup_help=provider.setup_help,
            settings=[
                MCPSettingSchema(
                    key=item.key,
                    label=item.label,
                    pattern=item.pattern,
                    required=item.required,
                    help=item.help,
                    placeholder=item.placeholder,
                )
                for item in provider.settings
            ],
            tools=list(provider.tools),
            auth_methods=list(provider.auth_methods),
            owner_selects_tools=provider.owner_selects_tools,
        )
        for provider in MCP_PROVIDERS.values()
    ]


@router.post("/connections/mcp", response=ConnectionSchema, auth=django_auth)
def create_mcp(
    request: HttpRequest, payload: MCPConnectionCreateSchema
) -> ConnectionSchema:
    workspace = require(request, Action.ADD_MCP_CONNECTIONS)
    try:
        connection = create_mcp_connection(
            workspace,
            payload.provider,
            payload.binding_key,
            payload.display_name,
            payload.token,
            payload.settings,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except MCPError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, "The MCP connection configuration is invalid.") from error
    return serialize_connection(connection)


@router.post(
    "/connections/{connection_id}/mcp",
    response=ConnectionSchema,
    auth=django_auth,
)
def update_mcp(
    request: HttpRequest, connection_id: int, payload: MCPConnectionUpdateSchema
) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, Action.EDIT_CONNECTIONS)
    try:
        connection = update_mcp_connection(
            connection,
            payload.display_name,
            payload.token,
            payload.settings,
            payload.tools,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except MCPError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, "The MCP connection configuration is invalid.") from error
    return serialize_connection(connection)


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
    return serialize_connection(connection)


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
    return serialize_connection(connection)


@router.post(
    "/connections/mcp/inspect", response=MCPInspectResultSchema, auth=django_auth
)
def inspect_mcp(
    request: HttpRequest, payload: MCPInspectSchema
) -> MCPInspectResultSchema:
    """Whether a server offers sign-in, and which scopes it lists."""
    require(request, Action.ADD_MCP_CONNECTIONS)
    try:
        server = inspect_server(payload.provider, payload.settings)
    except MCPError as error:
        raise HttpError(422, str(error)) from error
    if server is None:
        return MCPInspectResultSchema(oauth=False, scopes=[])
    return MCPInspectResultSchema(oauth=True, scopes=list(server.scopes_supported))


@router.post(
    "/connections/mcp/oauth/start", response=MCPSignInStartSchema, auth=django_auth
)
def start_mcp_sign_in(
    request: HttpRequest, payload: MCPSignInSchema
) -> MCPSignInStartSchema:
    workspace = require(request, Action.ADD_MCP_CONNECTIONS)
    connection = (
        get_object_or_404(
            Connection,
            pk=payload.connection_id,
            workspace=workspace,
            kind=Connection.Kind.MCP,
        )
        if payload.connection_id
        else None
    )
    if connection is None and not payload.binding_key:
        raise HttpError(422, "Enter a binding.")
    try:
        url = start_authorization(
            workspace,
            request.user,
            connection.provider if connection else payload.provider,
            payload.binding_key,
            payload.display_name,
            dict(connection.metadata.get("settings", {}))
            if connection
            else payload.settings,
            list(connection.scopes) if connection else payload.scopes,
            connection,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except MCPError as error:
        raise HttpError(422, str(error)) from error
    return MCPSignInStartSchema(authorize_url=url)


@router.get("/connections/oauth/callback", auth=None, include_in_schema=False)
def finish_mcp_sign_in(request: HttpRequest) -> HttpResponseRedirect:
    """The browser lands here from the sign-in page. Always redirect back."""
    back = f"{settings.FRONTEND_URL.rstrip('/')}/connections"
    if not request.user.is_authenticated:
        return HttpResponseRedirect(f"{back}?{urlencode({'oauth': 'invalid'})}")
    try:
        connection = complete_authorization(
            request.user,
            request.GET.get("state", "")[:200],
            request.GET.get("code", "")[:2000],
            request.GET.get("error", "")[:200],
        )
    # Only fixed codes go in the URL, so a crafted link cannot show any text.
    except SignInError as error:
        return HttpResponseRedirect(f"{back}?{urlencode({'oauth': error.code})}")
    except ConnectionConflict:
        return HttpResponseRedirect(f"{back}?{urlencode({'oauth': 'taken'})}")
    except (MCPError, ValidationError):
        return HttpResponseRedirect(f"{back}?{urlencode({'oauth': 'failed'})}")
    keys = connection.binding_keys()
    query = {"oauth": "connected", "capability": keys[0] if keys else ""}
    return HttpResponseRedirect(f"{back}?{urlencode(query)}")


@router.post("/connections/webhook", response=ConnectionSchema, auth=django_auth)
def create_webhook(
    request: HttpRequest,
    payload: WebhookConnectionCreateSchema,
) -> ConnectionSchema:
    workspace = require(request, Action.ADD_CONNECTIONS)
    try:
        connection = create_webhook_connection(
            workspace,
            payload.binding_key,
            payload.display_name,
            payload.url,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except WebhookError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return serialize_connection(connection)


def owned_connection(
    request: HttpRequest, connection_id: int, action: Action
) -> Connection:
    workspace = require(request, action)
    return get_object_or_404(Connection, pk=connection_id, workspace=workspace)


@router.post(
    "/connections/{connection_id}/webhook",
    response=ConnectionSchema,
    auth=django_auth,
)
def update_webhook(
    request: HttpRequest,
    connection_id: int,
    payload: WebhookConnectionUpdateSchema,
) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, Action.EDIT_CONNECTIONS)
    try:
        connection = update_webhook_connection(
            connection, payload.display_name, payload.url
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except WebhookError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return serialize_connection(connection)


@router.post("/connections/model/models", response=list[str], auth=django_auth)
def discover_model_provider_models(
    request: HttpRequest, payload: ModelConnectionCreateSchema
) -> list[str]:
    require(request, Action.ADD_MODEL_PROVIDERS)
    if (
        not payload.api_key.strip()
        and payload.provider != Connection.Provider.OPENAI_COMPATIBLE
    ):
        raise HttpError(422, "Enter an API key.")
    try:
        connection = Connection(
            provider=payload.provider,
            endpoint_url=validated_endpoint(payload.provider, payload.base_url),
            access_token=payload.api_key.strip(),
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
            else connection.access_token,
        )
        return fetch_models(preview)
    except (ModelProviderError, CheckFailed) as error:
        raise HttpError(422, str(error)) from error


@router.post("/connections/model", response=ConnectionSchema, auth=django_auth)
def create_model_provider(
    request: HttpRequest,
    payload: ModelConnectionCreateSchema,
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
            is_default=payload.is_default,
        )
    except ModelProviderError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return serialize_connection(connection)


@router.post(
    "/connections/{connection_id}/model",
    response=ConnectionSchema,
    auth=django_auth,
)
def update_model_provider(
    request: HttpRequest,
    connection_id: int,
    payload: ModelConnectionUpdateSchema,
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
            is_default=payload.is_default,
        )
    except ModelProviderError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, " ".join(error.messages)) from error
    return serialize_connection(connection)


@router.post(
    "/connections/{connection_id}/revoke",
    response=ConnectionSchema,
    auth=django_auth,
)
def revoke(request: HttpRequest, connection_id: int) -> ConnectionSchema:
    connection = owned_connection(request, connection_id, Action.DISCONNECT_CONNECTIONS)
    if connection.kind == Connection.Kind.IDENTITY:
        raise HttpError(409, "Sign-in connections are managed by their provider.")
    return serialize_connection(revoke_connection(connection))


@router.delete(
    "/connections/{connection_id}",
    response={204: None},
    auth=django_auth,
)
def remove_connection(request: HttpRequest, connection_id: int) -> Status[None]:
    connection = owned_connection(request, connection_id, Action.DELETE_CONNECTIONS)
    try:
        delete_connection(connection)
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    return Status(204, None)


@router.post(
    "/connections/{connection_id}/bindings",
    response=ConnectionSchema,
    auth=django_auth,
)
def add_binding(
    request: HttpRequest, connection_id: int, payload: ConnectionBindingSchema
) -> ConnectionSchema:
    """Let this connection also serve a binding. Each job still needs approval."""
    connection = owned_connection(request, connection_id, Action.EDIT_CONNECTIONS)
    if connection.status != Connection.Status.CONNECTED:
        raise HttpError(409, "Reconnect this connection before adding a binding.")
    try:
        bind(connection, payload.binding)
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    return serialize_connection(connection)


@router.delete(
    "/connections/{connection_id}/bindings/{binding}",
    response=ConnectionSchema,
    auth=django_auth,
)
def remove_binding(
    request: HttpRequest, connection_id: int, binding: str
) -> ConnectionSchema:
    """Stop serving one binding. Jobs that used it go back to pending."""
    connection = owned_connection(request, connection_id, Action.DISCONNECT_CONNECTIONS)
    try:
        unbind(connection, binding)
    except ConnectionConflict as error:
        raise HttpError(404, str(error)) from error
    return serialize_connection(connection)


@router.post(
    "/connections/{connection_id}/test",
    response=ConnectionTestSchema,
    auth=django_auth,
)
def test_connection(request: HttpRequest, connection_id: int) -> ConnectionTestSchema:
    connection = owned_connection(request, connection_id, Action.TEST_CONNECTIONS)
    if connection.kind == Connection.Kind.MCP:
        try:
            provider = mcp_provider(connection.provider)
            tools = discover_tools(connection)
            kept = [name for name in connection.available_tools if name in tools]
            select_tools(
                connection,
                provider,
                tools,
                kept if provider.owner_selects_tools else None,
            )
        except MCPError as error:
            return ConnectionTestSchema(ok=False, status_code=None, message=str(error))
        connection.save(update_fields=["available_tools", "metadata", "updated_at"])
        return ConnectionTestSchema(
            ok=True,
            status_code=None,
            message=f"Discovered {len(tools)} supported {provider.label} tools.",
        )
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
    if connection.provider != Connection.Provider.WEBHOOK:
        raise HttpError(400, "Testing is not available for this connection yet.")
    delivery = deliver_webhook(connection, connection_test_payload(connection))
    return ConnectionTestSchema(
        ok=delivery.ok,
        status_code=delivery.status_code,
        message=delivery.message,
    )


@router.get("/secrets", response=list[SecretSchema], auth=django_auth)
def secrets(request: HttpRequest) -> list[SecretSchema]:
    workspace = current_workspace(request)
    if not workspace:
        return []
    return [
        SecretSchema(
            name=item.name,
            description=item.description,
            last_used_at=item.last_used_at,
        )
        for item in Secret.objects.filter(workspace=workspace).defer("value")
    ]
