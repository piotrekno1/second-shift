"""OAuth sign-in for MCP servers: discovery, registration, PKCE, and refresh.

Follows the MCP authorization spec. Every URL a server advertises passes the
outbound check before SecondShift calls it. No token, verifier, or remote
error text leaves this module.
"""

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from secondshift.connections.mcp.base import MCPError, MCPProvider, MCPRequestError
from secondshift.connections.mcp.providers import mcp_provider
from secondshift.connections.mcp.transport import MCPTransport
from secondshift.connections.models import Connection
from secondshift.connections.outbound import OutboundUrlError, validate_outbound_url

OAUTH = "oauth"
CALLBACK_PATH = "/api/connections/oauth/callback"
AUTHORIZATION_TTL = timedelta(minutes=10)
# Refresh a little early so a token never expires mid-call.
REFRESH_MARGIN = timedelta(seconds=60)
DEFAULT_TOKEN_LIFETIME = timedelta(hours=1)
MAX_SCOPES = 20
SCOPE_PATTERN = r"[\x21\x23-\x5b\x5d-\x7e]{1,100}"
# Codes that mean the grant is gone, not that the server is briefly down.
GRANT_ERRORS = {"invalid_grant", "invalid_client", "unauthorized_client"}
CLIENT_NAME = "SecondShift"


class OAuthGrantError(MCPError):
    """The server refused the grant. The owner must sign in again."""


@dataclass(frozen=True)
class OAuthServer:
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    scopes_supported: tuple[str, ...]


def redirect_uri() -> str:
    return settings.FRONTEND_URL.rstrip("/") + CALLBACK_PATH


def checked_url(value: Any) -> str:
    """A URL the server advertised. Same rules as any other outbound call."""
    if not isinstance(value, str) or not value:
        raise MCPError("The server's sign-in settings are incomplete.")
    try:
        return validate_outbound_url(value, "sign-in URL", resolve=False)
    except OutboundUrlError:
        raise MCPError(
            "The server's sign-in settings use an address SecondShift cannot call."
        ) from None


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def request_json(
    method: str,
    url: str,
    *,
    data: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None, httpx.Headers]:
    """Status, parsed JSON body (or None), and headers. Never raises remote text."""
    try:
        with anyio.fail_after(settings.OUTBOUND_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=MCPTransport(),
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(method, url, data=data, json=payload)
                body = await response.aread()
    except Exception:
        raise MCPRequestError("The sign-in server could not be reached.") from None
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = None
    return (
        response.status_code,
        parsed if isinstance(parsed, dict) else None,
        response.headers,
    )


async def first_json(urls: list[str]) -> dict[str, Any] | None:
    for url in dict.fromkeys(urls):
        status, body, _ = await request_json("GET", url)
        if status == 200 and body:
            return body
    return None


def resource_metadata_hint(headers: httpx.Headers) -> str | None:
    challenge = headers.get("www-authenticate", "")
    match = re.search(r'resource_metadata="([^"]+)"', challenge)
    return match.group(1) if match else None


async def discover(resource_url: str) -> OAuthServer | None:
    """The server's OAuth settings, or None when it does not offer sign-in."""
    probe = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": "1"},
        },
    }
    status, _, headers = await request_json("POST", resource_url, payload=probe)
    candidates = []
    if status == 401 and (hint := resource_metadata_hint(headers)):
        candidates.append(checked_url(hint))
    path = urlsplit(resource_url).path.rstrip("/")
    candidates += [
        f"{origin(resource_url)}/.well-known/oauth-protected-resource{path}",
        f"{origin(resource_url)}/.well-known/oauth-protected-resource",
    ]
    resource = await first_json(candidates)
    if not resource:
        return None
    servers = resource.get("authorization_servers") or []
    canonical = str(resource.get("resource") or resource_url).rstrip("/")
    if not servers or not resource_url.rstrip("/").startswith(canonical):
        raise MCPError("The server's sign-in settings do not match its address.")
    issuer = checked_url(servers[0]).rstrip("/")
    issuer_path = urlsplit(issuer).path.rstrip("/")
    metadata = await first_json(
        [
            f"{origin(issuer)}/.well-known/oauth-authorization-server{issuer_path}",
            f"{origin(issuer)}/.well-known/openid-configuration{issuer_path}",
            f"{issuer}/.well-known/openid-configuration",
            f"{origin(issuer)}/.well-known/oauth-authorization-server",
        ]
    )
    if not metadata or str(metadata.get("issuer", "")).rstrip("/") != issuer:
        raise MCPError("The server's sign-in settings could not be verified.")
    if "S256" not in (metadata.get("code_challenge_methods_supported") or []):
        raise MCPError("The server does not support secure sign-in (PKCE).")
    if not metadata.get("registration_endpoint"):
        raise MCPError("The server does not let new apps register for sign-in.")
    scopes = resource.get("scopes_supported") or metadata.get("scopes_supported") or []
    return OAuthServer(
        resource=checked_url(canonical),
        issuer=issuer,
        authorization_endpoint=checked_url(metadata.get("authorization_endpoint")),
        token_endpoint=checked_url(metadata.get("token_endpoint")),
        registration_endpoint=checked_url(metadata.get("registration_endpoint")),
        scopes_supported=tuple(
            str(scope)
            for scope in scopes[:MAX_SCOPES]
            if re.fullmatch(SCOPE_PATTERN, str(scope))
        ),
    )


async def register_client(server: OAuthServer) -> str:
    """A public client: PKCE instead of a client secret."""
    status, body, _ = await request_json(
        "POST",
        server.registration_endpoint,
        payload={
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_uri()],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    if status not in {200, 201} or not body or not body.get("client_id"):
        raise MCPRequestError("The server did not register SecondShift for sign-in.")
    if (
        body.get("client_secret")
        or body.get("token_endpoint_auth_method", "none") != "none"
    ):
        raise MCPError(
            "The server requires a client secret, which is not supported yet."
        )
    return str(body["client_id"])


async def token_request(endpoint: str, form: dict[str, str]) -> dict[str, Any]:
    status, body, _ = await request_json("POST", endpoint, data=form)
    if status == 200 and body and body.get("access_token"):
        if str(body.get("token_type", "bearer")).lower() != "bearer":
            raise MCPError("The server issued an unsupported token type.")
        return body
    if status in {400, 401} and body and body.get("error") in GRANT_ERRORS:
        raise OAuthGrantError("The server no longer accepts this sign-in.")
    raise MCPRequestError("The sign-in server could not issue a token.")


def granted_scopes(tokens: dict[str, Any], requested: list[str]) -> list[str]:
    scope = tokens.get("scope")
    return sorted(str(scope).split()) if scope else sorted(requested)


def apply_tokens(connection: Connection, tokens: dict[str, Any]) -> None:
    connection.access_token = str(tokens["access_token"])
    if tokens.get("refresh_token"):
        connection.refresh_token = str(tokens["refresh_token"])
    try:
        lifetime = timedelta(seconds=int(tokens.get("expires_in", 0)))
    except (TypeError, ValueError):
        lifetime = timedelta()
    connection.expires_at = timezone.now() + (lifetime or DEFAULT_TOKEN_LIFETIME)


def inspect_server(provider_key: str, values: dict[str, str]) -> OAuthServer | None:
    provider = mcp_provider(provider_key)
    if OAUTH not in provider.auth_methods:
        return None
    resource_url = provider.endpoint(provider.clean_settings(values))
    return async_to_sync(discover)(resource_url)


def expire(connection: Connection) -> None:
    Connection.objects.filter(pk=connection.pk).update(
        status=Connection.Status.EXPIRED, updated_at=timezone.now()
    )
    connection.status = Connection.Status.EXPIRED


def fresh_connection(connection: Connection) -> Connection:
    """Refresh an OAuth token that expires soon. Updates `connection` in place.

    The row is locked so parallel tool calls refresh once. A refresh token can
    be single use, so a refused refresh is checked against the saved row first.
    A refused grant marks the connection expired after the lock is released,
    so the status change is not rolled back with the error.
    """
    if (
        connection.auth_strategy != Connection.AuthStrategy.OAUTH
        or connection.pk is None
        or connection.status != Connection.Status.CONNECTED
        or (
            connection.expires_at
            and connection.expires_at - REFRESH_MARGIN > timezone.now()
        )
    ):
        return connection
    provider: MCPProvider = mcp_provider(connection.provider)
    with transaction.atomic():
        failure = refresh_locked(connection)
    if failure:
        expire(connection)
        raise MCPRequestError(failure.format(label=provider.label))
    return connection


def refresh_locked(connection: Connection) -> str | None:
    """Refresh under a row lock. Returns why the sign-in can no longer be used."""
    expired = "The {label} sign-in expired. Reconnect it."
    locked = Connection.objects.select_for_update().get(pk=connection.pk)
    if locked.expires_at and locked.expires_at - REFRESH_MARGIN > timezone.now():
        copy_tokens(locked, connection)
        return None
    oauth = locked.metadata.get("oauth", {})
    if not locked.refresh_token or not oauth.get("token_endpoint"):
        return expired
    used_refresh_token = locked.refresh_token
    try:
        tokens = async_to_sync(token_request)(
            oauth["token_endpoint"],
            {
                "grant_type": "refresh_token",
                "refresh_token": used_refresh_token,
                "client_id": oauth["client_id"],
                "resource": oauth.get("resource", ""),
            },
        )
    except OAuthGrantError:
        latest = Connection.objects.get(pk=connection.pk)
        if latest.refresh_token != used_refresh_token:
            copy_tokens(latest, connection)
            return None
        return expired
    if set(granted_scopes(tokens, locked.scopes)) - set(locked.scopes):
        return "{label} granted more access than approved. Reconnect it."
    apply_tokens(locked, tokens)
    locked.save(
        update_fields=["access_token", "refresh_token", "expires_at", "updated_at"]
    )
    copy_tokens(locked, connection)
    return None


def copy_tokens(source: Connection, target: Connection) -> None:
    target.access_token = source.access_token
    target.refresh_token = source.refresh_token
    target.expires_at = source.expires_at
    target.status = source.status
