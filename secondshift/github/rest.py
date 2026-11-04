"""The one way SecondShift calls GitHub's REST API.

Errors raised here never include the token, the request, or GitHub's reply.
"""

from typing import Any

import httpx

from secondshift.config.constants import GITHUB_API_VERSION
from secondshift.github.errors import GitHubError

PAGE_SIZE = 100
MAX_PAGES = 100
TIMEOUT_SECONDS = 30


def headers(token: str = "") -> dict[str, str]:
    return {
        "Accept": "application/json",
        **({"Authorization": f"Bearer {token}"} if token else {}),
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def send(method: str, url: str, token: str = "", **kwargs: Any) -> httpx.Response:
    """The raw response, for callers that read GitHub's status codes."""
    try:
        with client() as session:
            return session.request(method, url, headers=headers(token), **kwargs)
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise GitHubError("GitHub could not be reached.") from error


def request_payload(method: str, url: str, token: str = "", **kwargs: Any) -> object:
    try:
        response = send(method, url, token, **kwargs)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError, GitHubError) as error:
        raise GitHubError(
            "GitHub access failed. Check the installation and permissions."
        ) from error


def request_json(method: str, url: str, token: str = "", **kwargs: Any) -> dict:
    result = request_payload(method, url, token, **kwargs)
    if not isinstance(result, dict) or "error" in result:
        raise GitHubError("GitHub returned an invalid authorization response.")
    return result


def paginated(url: str, token: str, key: str | None, too_many: str) -> list[Any]:
    """Every item of a listing, page by page. key names the list in each page."""
    items: list[Any] = []
    separator = "&" if "?" in url else "?"
    for page in range(1, MAX_PAGES + 1):
        payload = request_payload(
            "GET", f"{url}{separator}per_page={PAGE_SIZE}&page={page}", token
        )
        batch = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(batch, list):
            raise GitHubError("GitHub returned an invalid listing.")
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            return items
    raise GitHubError(too_many)


def parsed(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def reason(response: httpx.Response) -> str:
    """GitHub's own short reason, which never contains the token."""
    payload = parsed(response)
    errors = payload.get("errors") if isinstance(payload, dict) else None
    details = [
        str(item.get("message"))
        for item in errors or []
        if isinstance(item, dict) and item.get("message")
    ]
    message = details[0] if details else ""
    if not message and isinstance(payload, dict):
        message = str(payload.get("message", ""))
    return (message or f"HTTP {response.status_code}")[:200]
