"""API tokens: how MCP clients sign in as a person in one workspace.

A token is shown once, when it is created. The database keeps a hash of it.
A token works until it expires or is revoked, and only while its creator
still belongs to its workspace. Its scope limits what it can change. See
TOKEN_ACTIONS in secondshift.workspaces.access.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from ninja.security import HttpBearer

from secondshift.workspaces.access import (
    API_TOKEN_ATTRIBUTE,
    MEMBERSHIP_ATTRIBUTE,
    TOKEN_ACTIONS,
    Role,
    role_allows,
)
from secondshift.workspaces.models import ApiToken, WorkspaceMembership

# Secret scanners can match tokens by this prefix.
TOKEN_PREFIX = "ss_"
# Last use is saved at most this often, so each call does not write.
LAST_USE_PRECISION = timedelta(minutes=1)


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class NewApiToken:
    token: ApiToken
    # Only here, right after creating it. It is never stored.
    secret: str


def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def usable_tokens() -> QuerySet[ApiToken]:
    return ApiToken.objects.filter(
        revoked_at__isnull=True, expires_at__gt=timezone.now()
    )


def scope_allowed(role: str, scope: str) -> bool:
    """Whether a role may create a token with this scope."""
    return all(
        role_allows(role, action) for action in TOKEN_ACTIONS[ApiToken.Scope(scope)]
    )


def create_api_token(
    membership: WorkspaceMembership, name: str, scope: str, days: int
) -> NewApiToken:
    if not 1 <= days <= settings.API_TOKEN_MAX_DAYS:
        raise TokenError(
            f"Choose an expiry between 1 and {settings.API_TOKEN_MAX_DAYS} days."
        )
    if not scope_allowed(membership.role, scope):
        raise TokenError("Only workspace owners and admins can create run tokens.")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken.objects.create(
        workspace_id=membership.workspace_id,
        created_by_id=membership.user_id,
        name=name,
        scope=ApiToken.Scope(scope),
        token_hash=token_hash(secret),
        expires_at=timezone.now() + timedelta(days=days),
    )
    return NewApiToken(token, secret)


def visible_tokens(membership: WorkspaceMembership) -> QuerySet[ApiToken]:
    """Owners see every usable token in the workspace. Others see their own."""
    tokens = usable_tokens().filter(workspace_id=membership.workspace_id)
    if membership.role != Role.OWNER:
        tokens = tokens.filter(created_by_id=membership.user_id)
    return tokens.select_related("created_by")


def revoke_api_token(token: ApiToken) -> None:
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])


def find_api_token(secret: str) -> ApiToken | None:
    """The token for a secret while it works: usable, creator still a member."""
    return (
        usable_tokens()
        .filter(
            token_hash=token_hash(secret),
            created_by__is_active=True,
            created_by__workspace_memberships__workspace_id=F("workspace_id"),
        )
        .select_related("created_by", "workspace")
        .first()
    )


def record_use(token: ApiToken) -> None:
    now = timezone.now()
    stale = Q(last_used_at__isnull=True) | Q(last_used_at__lt=now - LAST_USE_PRECISION)
    ApiToken.objects.filter(stale, pk=token.pk).update(last_used_at=now)


class ApiTokenAuth(HttpBearer):
    """Sign the request in as the token's creator, in the token's workspace."""

    def authenticate(self, request: HttpRequest, token: str) -> ApiToken | None:
        api_token = find_api_token(token)
        if api_token is None:
            return None
        request.user = api_token.created_by
        setattr(request, API_TOKEN_ATTRIBUTE, api_token)
        # Forget any membership resolved from the session before this point.
        if hasattr(request, MEMBERSHIP_ATTRIBUTE):
            delattr(request, MEMBERSHIP_ATTRIBUTE)
        record_use(api_token)
        return api_token
