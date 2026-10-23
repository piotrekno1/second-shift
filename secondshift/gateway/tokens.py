"""Per-run gateway credentials. Only a token's SHA-256 is ever stored."""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone

from secondshift.automations.models import Run
from secondshift.gateway.models import RunToken
from secondshift.integrations.models import Connection

TOKEN_PREFIX = "ssrt_"
ACTIVE_RUN_STATUSES = (Run.Status.QUEUED, Run.Status.RUNNING)


class GatewayUnavailable(Exception):
    pass


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def default_model_connection(run: Run) -> Connection | None:
    return Connection.objects.filter(
        workspace_id=run.job.workspace_id,
        kind=Connection.Kind.MODEL,
        is_default=True,
        status=Connection.Status.CONNECTED,
    ).first()


def issue_run_token(run: Run, connection: Connection | None = None) -> str:
    """Create a gateway token for one run. The plaintext is returned once."""
    connection = connection or default_model_connection(run)
    if connection is None:
        raise GatewayUnavailable("No connected default model provider.")
    if (
        connection.kind != Connection.Kind.MODEL
        or connection.workspace_id != run.job.workspace_id
    ):
        raise GatewayUnavailable("The model provider belongs to another workspace.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    RunToken.objects.create(
        run=run,
        connection=connection,
        token_hash=token_digest(token),
        expires_at=timezone.now()
        + timedelta(seconds=settings.RUN_TIMEOUT_SECONDS + 300),
        budget_tokens=settings.MODEL_RUN_TOKEN_BUDGET,
    )
    return token


def revoke_run_tokens(run_id: int) -> int:
    return RunToken.objects.filter(run_id=run_id, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def active_tokens(token: str) -> QuerySet[RunToken]:
    return RunToken.objects.select_related("connection", "run__job").filter(
        token_hash=token_digest(token),
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
        run__status__in=ACTIVE_RUN_STATUSES,
        connection__status=Connection.Status.CONNECTED,
        connection__kind=Connection.Kind.MODEL,
    )


def same_workspace(run_token: RunToken | None) -> RunToken | None:
    if run_token is None:
        return None
    if run_token.connection.workspace_id != run_token.run.job.workspace_id:
        return None
    return run_token


def authenticate(token: str) -> RunToken | None:
    if not token.startswith(TOKEN_PREFIX):
        return None
    return same_workspace(active_tokens(token).first())


async def aauthenticate(token: str) -> RunToken | None:
    if not token.startswith(TOKEN_PREFIX):
        return None
    return same_workspace(await active_tokens(token).afirst())


def request_token(request: HttpRequest) -> str:
    # Anthropic SDKs send x-api-key; OpenAI SDKs send a bearer token.
    if api_key := request.headers.get("x-api-key"):
        return api_key.strip()
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""
