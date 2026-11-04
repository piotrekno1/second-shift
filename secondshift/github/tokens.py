"""Check a personal access token before SecondShift stores it."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from secondshift.config.constants import (
    GITHUB_API_URL,
    GITHUB_FINE_GRAINED_TOKEN_PATTERN,
    GITHUB_TOKEN_HINT,
)
from secondshift.github.errors import GitHubError
from secondshift.github.rest import send

# GitHub sends a token's expiry with every response it answers with that token.
EXPIRY_HEADER = "github-authentication-token-expiration"


@dataclass(frozen=True)
class TokenOwner:
    login: str
    expires_at: datetime | None


def inspect_token(token: str) -> TokenOwner:
    """Whose token this is and when it expires. Refuses classic tokens."""
    if not re.fullmatch(GITHUB_FINE_GRAINED_TOKEN_PATTERN, token):
        raise GitHubError(GITHUB_TOKEN_HINT)
    response = send("GET", f"{GITHUB_API_URL}/user", token)
    if response.status_code == 401:
        raise GitHubError("GitHub refused this token. It may have expired.")
    try:
        response.raise_for_status()
        login = response.json()["login"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise GitHubError("GitHub could not check this token.") from error
    if not isinstance(login, str) or not login:
        raise GitHubError("GitHub could not check this token.")
    return TokenOwner(login, expiry(response.headers.get(EXPIRY_HEADER, "")))


def expiry(value: str) -> datetime | None:
    """Parse "2026-12-01 08:00:00 UTC" or "... -0800". None for no expiry."""
    value = value.strip().replace(" UTC", " +0000")
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z").astimezone(UTC)
    except ValueError:
        return None
