"""Open draft pull requests through the REST API."""

from typing import Any

import httpx

from secondshift.config.constants import GITHUB_API_URL
from secondshift.github.errors import GitHubError
from secondshift.github.rest import parsed, reason, send

# GitHub refuses a longer description.
MAX_BODY_CHARS = 60_000


def open_draft(
    token: str, full_name: str, title: str, head: str, base: str, body: str
) -> dict[str, Any]:
    """Open the draft, or find the one an earlier attempt opened."""
    url = f"{GITHUB_API_URL}/repos/{full_name}/pulls"
    created = send(
        "POST",
        url,
        token,
        json={
            "title": title,
            "head": head,
            "base": base,
            "body": body[:MAX_BODY_CHARS],
            "draft": True,
        },
    )
    if created.status_code == 201:
        return pull_request(created)
    owner = full_name.split("/", 1)[0]
    existing = send(
        "GET", url, token, params={"head": f"{owner}:{head}", "state": "all"}
    )
    found = parsed(existing) if existing.status_code == 200 else []
    if isinstance(found, list) and found:
        return checked(found[0])
    raise GitHubError(f"GitHub did not open the pull request: {reason(created)}")


def pull_request(response: httpx.Response) -> dict[str, Any]:
    return checked(parsed(response))


def checked(payload: object) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("html_url"), str)
        or not isinstance(payload.get("number"), int)
    ):
        raise GitHubError("GitHub returned an invalid pull request.")
    return payload
