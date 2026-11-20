"""Open issues, comment on them and change their labels through the REST API."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from secondshift.github.errors import GitHubError
from secondshift.github.rest import GITHUB_API_URL, parsed, reason, send

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


def open_issue(
    token: str, full_name: str, title: str, body: str, labels: list[str]
) -> Issue:
    payload: dict[str, Any] = {
        "title": title[:MAX_TITLE_CHARS],
        "body": body[:MAX_BODY_CHARS],
    }
    if labels:
        payload["labels"] = labels
    created = send(
        "POST", f"{GITHUB_API_URL}/repos/{full_name}/issues", token, json=payload
    )
    if created.status_code != 201:
        raise GitHubError(f"GitHub did not open the issue: {reason(created)}")
    return checked(parsed(created))


@dataclass(frozen=True)
class IssueState:
    open: bool
    labels: tuple[str, ...]
    # Pull requests are issues too in this API.
    pull_request: bool


def issue_url(full_name: str, number: int) -> str:
    return f"{GITHUB_API_URL}/repos/{full_name}/issues/{number}"


def issue_state(token: str, full_name: str, number: int) -> IssueState | None:
    """Whether the issue is open and its labels, or None if there is no such issue."""
    response = send("GET", issue_url(full_name, number), token)
    if response.status_code in (404, 410):
        return None
    if response.status_code != 200:
        raise GitHubError(f"GitHub could not read the issue: {reason(response)}")
    payload = parsed(response)
    if not isinstance(payload, dict):
        raise GitHubError("GitHub returned an invalid issue.")
    items = payload.get("labels")
    labels = [
        str(item["name"])
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and item.get("name")
    ]
    return IssueState(
        open=payload.get("state") == "open",
        labels=tuple(labels),
        pull_request="pull_request" in payload,
    )


def comment_on_issue(token: str, full_name: str, number: int, body: str) -> str:
    """The new comment's URL."""
    created = send(
        "POST",
        f"{issue_url(full_name, number)}/comments",
        token,
        json={"body": body[:MAX_BODY_CHARS]},
    )
    if created.status_code != 201:
        raise GitHubError(f"GitHub did not add the comment: {reason(created)}")
    payload = parsed(created)
    url = payload.get("html_url") if isinstance(payload, dict) else None
    if not isinstance(url, str):
        raise GitHubError("GitHub returned an invalid comment.")
    return url


def remove_issue_label(token: str, full_name: str, number: int, label: str) -> None:
    """Remove a label. A label the issue doesn't have is already gone."""
    response = send(
        "DELETE",
        f"{issue_url(full_name, number)}/labels/{quote(label, safe='')}",
        token,
    )
    if response.status_code not in (200, 204, 404):
        raise GitHubError(f"GitHub did not remove the label: {reason(response)}")


def add_issue_labels(
    token: str, full_name: str, number: int, labels: list[str]
) -> None:
    """Add labels. GitHub creates a label the repository doesn't have yet."""
    response = send(
        "POST", f"{issue_url(full_name, number)}/labels", token, json={"labels": labels}
    )
    if response.status_code != 200:
        raise GitHubError(f"GitHub did not add the labels: {reason(response)}")


def checked(payload: Any) -> Issue:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("html_url"), str)
        or not isinstance(payload.get("number"), int)
    ):
        raise GitHubError("GitHub returned an invalid issue.")
    return Issue(payload["number"], payload["html_url"])
