"""Find the App installations a person can use, through their own sign-in.

The person's token proves which installations they may link. It is used for
two requests and never stored.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings

from secondshift.config.constants import (
    GITHUB_API_URL,
    GITHUB_BASE_URL,
    GITHUB_OAUTH_URL,
)
from secondshift.github.errors import GitHubError
from secondshift.github.rest import paginated, request_json


@dataclass(frozen=True)
class Installation:
    installation_id: str
    account: str


def authorization_url(state: str, redirect_uri: str) -> str:
    return f"{GITHUB_OAUTH_URL}/authorize?" + urlencode(
        {
            "client_id": settings.GITHUB_APP_CLIENT_ID,
            "state": state,
            "redirect_uri": redirect_uri,
        }
    )


def installation_url(state: str) -> str:
    return (
        f"{GITHUB_BASE_URL}/apps/{settings.GITHUB_APP_SLUG}/installations/new?"
        + urlencode({"state": state})
    )


def user_token(code: str, redirect_uri: str) -> str:
    data = request_json(
        "POST",
        f"{GITHUB_OAUTH_URL}/access_token",
        json={
            "client_id": settings.GITHUB_APP_CLIENT_ID,
            "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    token = data.get("access_token")
    if not token:
        raise GitHubError("GitHub did not authorize this user.")
    return token


def user_installations(token: str) -> list[Installation]:
    """This App's installations that the person can reach. Other apps are skipped."""
    listed = paginated(
        f"{GITHUB_API_URL}/user/installations",
        token,
        "installations",
        "You can reach too many GitHub App installations to list.",
    )
    return [
        Installation(str(item["id"]), item["account"]["login"])
        for item in listed
        if isinstance(item, dict)
        and str(item.get("app_id")) == str(settings.GITHUB_APP_ID)
    ]
