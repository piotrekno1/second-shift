"""MCP server connections, including sign-in with OAuth."""

from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.api.shared import apply_approval, owned_connection
from secondshift.connections.dao import workspace_connections
from secondshift.connections.mcp.base import MCPError
from secondshift.connections.mcp.client import (
    create_mcp_connection,
    update_mcp_connection,
)
from secondshift.connections.mcp.oauth import (
    inspect_server,
    requested_scopes,
)
from secondshift.connections.mcp.providers import MCP_PROVIDERS
from secondshift.connections.mcp.signin import (
    SignInError,
    complete_authorization,
    start_authorization,
)
from secondshift.connections.models import Connection
from secondshift.connections.schemas import (
    ConnectionSchema,
    MCPConnectionCreateSchema,
    MCPConnectionUpdateSchema,
    MCPInspectResultSchema,
    MCPInspectSchema,
    MCPProviderSchema,
    MCPSettingSchema,
    MCPSignInSchema,
    MCPSignInStartSchema,
)
from secondshift.connections.services import ConnectionConflict
from secondshift.jobs.automatic_grants import approve_waiting_for
from secondshift.workspaces.access import Action, require

router = Router(auth=django_auth, tags=["connections"])


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
            payload.auth,
        )
    except ConnectionConflict as error:
        raise HttpError(409, str(error)) from error
    except MCPError as error:
        raise HttpError(422, str(error)) from error
    except ValidationError as error:
        raise HttpError(422, "The MCP connection configuration is invalid.") from error
    return apply_approval(connection, payload.require_approval)


@router.post(
    "/connections/{connection_id}/mcp", response=ConnectionSchema, auth=django_auth
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
    return apply_approval(connection, payload.require_approval)


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
            workspace_connections(workspace.pk, Connection.Kind.MCP),
            pk=payload.connection_id,
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
            connection.settings if connection else payload.settings,
            requested_scopes(connection) if connection else payload.scopes,
            connection,
            payload.require_approval,
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
    approve_waiting_for(connection)
    query = {"oauth": "connected", "capability": connection.binding_key}
    return HttpResponseRedirect(f"{back}?{urlencode(query)}")
