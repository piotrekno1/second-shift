"""Browser sign-in for MCP connections: start, then finish on the callback."""

import logging
import re
import secrets
from urllib.parse import urlencode

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AbstractBaseUser
from django.db import IntegrityError, transaction
from django.utils import timezone

from secondshift.core.models import Workspace
from secondshift.integrations.mcp.base import MCPError
from secondshift.integrations.mcp.client import (
    connection_target,
    discover_tools,
    select_tools,
)
from secondshift.integrations.mcp.oauth import (
    AUTHORIZATION_TTL,
    MAX_SCOPES,
    OAUTH,
    SCOPE_PATTERN,
    apply_tokens,
    discover,
    granted_scopes,
    pkce_pair,
    redirect_uri,
    register_client,
    token_request,
)
from secondshift.integrations.mcp.providers import mcp_provider
from secondshift.integrations.models import Connection, OAuthAuthorization
from secondshift.integrations.services import ConnectionConflict

logger = logging.getLogger(__name__)


class SignInError(MCPError):
    """A sign-in that cannot finish. `code` is safe to put in a redirect URL."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def start_authorization(
    workspace: Workspace,
    user: AbstractBaseUser,
    provider_key: str,
    binding_key: str,
    display_name: str,
    values: dict[str, str],
    scopes: list[str],
    connection: Connection | None = None,
) -> str:
    """Record a pending sign-in and return the address to send the browser to."""
    provider = mcp_provider(provider_key)
    if OAUTH not in provider.auth_methods:
        raise MCPError(f"{provider.label} does not support sign-in.")
    if (
        connection is None
        and Connection.objects.filter(
            workspace=workspace, binding_key=binding_key
        ).exists()
    ):
        raise ConnectionConflict("This binding already has a connection.")
    cleaned = provider.clean_settings(values)
    server = async_to_sync(discover)(provider.endpoint(cleaned))
    if server is None:
        raise MCPError("This server does not offer sign-in. Use a token instead.")
    scopes = sorted(set(scopes))
    if len(scopes) > MAX_SCOPES or any(
        not re.fullmatch(SCOPE_PATTERN, scope)
        or (server.scopes_supported and scope not in server.scopes_supported)
        for scope in scopes
    ):
        raise MCPError("Choose scopes from the server's list.")
    client_id = async_to_sync(register_client)(server)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    OAuthAuthorization.objects.filter(
        user=user, created_at__lt=timezone.now() - AUTHORIZATION_TTL
    ).delete()
    OAuthAuthorization.objects.create(
        workspace=workspace,
        user=user,
        connection=connection,
        state=state,
        code_verifier=verifier,
        provider=provider.key,
        binding_key=connection.binding_key if connection else binding_key,
        display_name=display_name,
        connection_settings=cleaned,
        scopes=scopes,
        oauth={
            "issuer": server.issuer,
            "token_endpoint": server.token_endpoint,
            "client_id": client_id,
            "resource": server.resource,
        },
    )
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": server.resource,
    }
    if scopes:
        query["scope"] = " ".join(scopes)
    separator = "&" if "?" in server.authorization_endpoint else "?"
    return f"{server.authorization_endpoint}{separator}{urlencode(query)}"


def claim_authorization(user: AbstractBaseUser, state: str) -> OAuthAuthorization:
    """Single use: a state value works once, for the person who started it."""
    with transaction.atomic():
        authorization = (
            # Lock only this row. Postgres cannot lock the optional connection join.
            OAuthAuthorization.objects.select_for_update(of=("self",))
            .select_related("connection")
            .filter(state=state, user=user, used_at__isnull=True)
            .first()
        )
        if authorization is None:
            raise SignInError("invalid", "This sign-in is not valid. Start again.")
        authorization.used_at = timezone.now()
        authorization.save(update_fields=["used_at"])
    if authorization.created_at < timezone.now() - AUTHORIZATION_TTL:
        raise SignInError("expired", "This sign-in expired. Start again.")
    return authorization


def complete_authorization(
    user: AbstractBaseUser, state: str, code: str, error: str
) -> Connection:
    authorization = claim_authorization(user, state)
    if error or not code:
        raise SignInError("refused", "Sign-in was cancelled or refused.")
    provider = mcp_provider(authorization.provider)
    oauth = authorization.oauth
    tokens = async_to_sync(token_request)(
        oauth["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
            "client_id": oauth["client_id"],
            "code_verifier": authorization.code_verifier,
            "resource": oauth["resource"],
        },
    )
    granted = granted_scopes(tokens, authorization.scopes)
    if set(granted) - set(authorization.scopes):
        # Scope names are not secret. Logged so an owner can see what to untick.
        logger.warning(
            "MCP sign-in refused: requested scopes %s, server granted %s",
            sorted(authorization.scopes),
            [scope for scope in granted if re.fullmatch(SCOPE_PATTERN, scope)][:20],
        )
        raise SignInError(
            "scope", "The server granted more access than requested. Nothing was saved."
        )
    connection = authorization.connection or Connection(
        workspace=authorization.workspace,
        provider=provider.key,
        kind=Connection.Kind.MCP,
        binding_key=authorization.binding_key,
    )
    if connection.workspace_id != authorization.workspace_id:
        raise SignInError("invalid", "This sign-in is not valid. Start again.")
    cleaned = authorization.connection_settings
    connection.display_name = (
        authorization.display_name.strip() or connection.display_name or provider.label
    )
    connection.endpoint_url = provider.endpoint(cleaned)
    connection.auth_strategy = Connection.AuthStrategy.OAUTH
    connection.status = Connection.Status.CONNECTED
    connection.scopes = granted
    connection.refresh_token = ""
    apply_tokens(connection, tokens)
    connection.metadata = {
        **connection.metadata,
        "target": connection_target(provider, cleaned),
        "settings": cleaned,
        "oauth": oauth,
    }
    tools = discover_tools(connection)
    select_tools(
        connection,
        provider,
        tools,
        connection.available_tools if authorization.connection else None,
    )
    connection.full_clean()
    try:
        with transaction.atomic():
            connection.save()
    except IntegrityError:
        raise ConnectionConflict("This binding already has a connection.") from None
    return connection
