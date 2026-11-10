"""Per-run gateway credentials. Only a token's SHA-256 is ever stored."""

import hashlib
import secrets
from datetime import datetime

from django.http import HttpRequest

from secondshift.connections.models import Connection
from secondshift.gateway.dao import aactive_token, active_token, create_run_token
from secondshift.gateway.models import RunToken

TOKEN_PREFIX = "ssrt_"


class GatewayUnavailable(Exception):
    pass


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(
    run_id: int,
    workspace_id: int,
    connection: Connection,
    expires_at: datetime,
    budget_tokens: int,
) -> str:
    """Create a gateway token for one run. The plaintext is returned once."""
    if (
        connection.kind != Connection.Kind.MODEL
        or connection.workspace_id != workspace_id
    ):
        raise GatewayUnavailable("The model provider belongs to another workspace.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    create_run_token(run_id, connection, token_digest(token), expires_at, budget_tokens)
    return token


def same_workspace(run_token: RunToken | None) -> RunToken | None:
    if run_token is None:
        return None
    if run_token.connection.workspace_id != run_token.run.workspace_id:
        return None
    return run_token


def authenticate(token: str) -> RunToken | None:
    if not token.startswith(TOKEN_PREFIX):
        return None
    return same_workspace(active_token(token_digest(token)))


async def aauthenticate(token: str) -> RunToken | None:
    if not token.startswith(TOKEN_PREFIX):
        return None
    return same_workspace(await aactive_token(token_digest(token)))


def request_token(request: HttpRequest) -> str:
    # Anthropic SDKs send x-api-key. OpenAI SDKs send a bearer token.
    if api_key := request.headers.get("x-api-key"):
        return api_key.strip()
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""
