"""Open issues through the REST API."""

from dataclasses import dataclass
from typing import Any

from secondshift.config.constants import GITHUB_API_URL
from secondshift.github.errors import GitHubError
from secondshift.github.rest import parsed, reason, send

# GitHub refuses a longer title or body.
MAX_TITLE_CHARS = 256
MAX_BODY_CHARS = 65_000


@dataclass(frozen=True)
class Issue:
    number: int
    url: str


def same_title(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def open_issue_titled(token: str, full_name: str, title: str) -> Issue | None:
    """An open issue with this exact title, so a weekly job does not repeat it.

    Search is a little behind new issues, so this catches older duplicates,
    not one opened seconds ago.
    """
    words = title.replace('"', " ").strip()
    response = send(
        "GET",
        f"{GITHUB_API_URL}/search/issues",
        token,
        params={
            "q": f'repo:{full_name} is:issue is:open in:title "{words}"',
            "per_page": 20,
        },
    )
    if response.status_code != 200:
        raise GitHubError(f"GitHub could not search issues: {reason(response)}")
    payload = parsed(response)
    items = payload.get("items") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and same_title(str(item.get("title", "")), title):
            return checked(item)
    return None


def open_issue(token: str, full_name: str, title: str, body: str) -> Issue:
    created = send(
        "POST",
        f"{GITHUB_API_URL}/repos/{full_name}/issues",
        token,
        json={"title": title[:MAX_TITLE_CHARS], "body": body[:MAX_BODY_CHARS]},
    )
    if created.status_code != 201:
        raise GitHubError(f"GitHub did not open the issue: {reason(created)}")
    return checked(parsed(created))


def checked(payload: Any) -> Issue:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("html_url"), str)
        or not isinstance(payload.get("number"), int)
    ):
        raise GitHubError("GitHub returned an invalid issue.")
    return Issue(payload["number"], payload["html_url"])
