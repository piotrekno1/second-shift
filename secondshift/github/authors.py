"""Who SecondShift's commits say wrote them.

A commit names the same GitHub account that pushes it and opens the pull
request. GitHub links a commit to an account by its noreply address, which
holds the account's ID and login. An address SecondShift made up could link
the commit to a stranger.
"""

from dataclasses import dataclass
from urllib.parse import quote

from django.conf import settings

from secondshift.config.constants import GITHUB_API_URL
from secondshift.github.errors import GitHubError
from secondshift.github.rest import request_json

NOREPLY_DOMAIN = "users.noreply.github.com"
BOT_SUFFIX = "[bot]"


@dataclass(frozen=True)
class GitAuthor:
    name: str
    email: str


def noreply_email(account_id: int, login: str) -> str:
    return f"{account_id}+{login}@{NOREPLY_DOMAIN}"


def account_author(data: dict, name: str | None = None) -> GitAuthor:
    account_id = data.get("id")
    login = data.get("login")
    if not isinstance(account_id, int) or not isinstance(login, str) or not login:
        raise GitHubError("GitHub did not say which account the token belongs to.")
    return GitAuthor(name or login, noreply_email(account_id, login))


def app_author(token: str) -> GitAuthor:
    """The App's bot account, such as secondshift-acme[bot]."""
    login = f"{settings.GITHUB_APP_SLUG}{BOT_SUFFIX}"
    data = request_json("GET", f"{GITHUB_API_URL}/users/{quote(login)}", token)
    return account_author(data, login)


def token_author(token: str) -> GitAuthor:
    """The person who owns a personal access token."""
    data = request_json("GET", f"{GITHUB_API_URL}/user", token)
    name = data.get("name")
    return account_author(data, name if isinstance(name, str) and name else None)
