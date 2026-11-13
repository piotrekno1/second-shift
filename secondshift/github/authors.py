"""Who SecondShift's commits say wrote them.

A commit names the same GitHub account that pushes it and opens the pull
request. GitHub links a commit to an account by its noreply address, which
holds the account's ID and login. An address SecondShift made up could link
the commit to a stranger.

The name is the login, which only changes when the account is renamed. A
retry then builds the same commit as the attempt before it.
"""

from dataclasses import dataclass
from urllib.parse import quote

from secondshift.config.constants import GITHUB_API_URL
from secondshift.github import app
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


def account_author(data: dict) -> GitAuthor:
    account_id = data.get("id")
    login = data.get("login")
    if not isinstance(account_id, int) or not isinstance(login, str) or not login:
        raise GitHubError("GitHub did not say which account publishes the changes.")
    return GitAuthor(login, noreply_email(account_id, login))


def app_author(token: str) -> GitAuthor:
    """The App's bot account, such as secondshift-acme[bot].

    GitHub names the App, so renaming it never leaves a stale slug here.
    """
    slug = request_json("GET", f"{GITHUB_API_URL}/app", app.app_token()).get("slug")
    if not isinstance(slug, str) or not slug:
        raise GitHubError("GitHub did not say which account publishes the changes.")
    login = quote(f"{slug}{BOT_SUFFIX}")
    return account_author(request_json("GET", f"{GITHUB_API_URL}/users/{login}", token))


def token_author(token: str) -> GitAuthor:
    """The person who owns a personal access token."""
    return account_author(request_json("GET", f"{GITHUB_API_URL}/user", token))
