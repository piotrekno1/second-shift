"""Server-owned GitHub App credentials and installation-scoped API access."""

import base64
import json
import time
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings

from secondshift.github.errors import GitHubError

API_URL = "https://api.github.com"
OAUTH_URL = "https://github.com/login/oauth"


def configured() -> bool:
    return all(
        (
            settings.GITHUB_APP_ID,
            settings.GITHUB_APP_SLUG,
            settings.GITHUB_APP_PRIVATE_KEY,
            settings.GITHUB_APP_CLIENT_ID,
            settings.GITHUB_APP_CLIENT_SECRET,
        )
    )


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def app_token() -> str:
    now = int(time.time())
    header = encoded(b'{"alg":"RS256","typ":"JWT"}')
    payload = encoded(
        json.dumps(
            {"iat": now - 60, "exp": now + 540, "iss": settings.GITHUB_APP_ID}
        ).encode()
    )
    message = f"{header}.{payload}"
    try:
        key = serialization.load_pem_private_key(
            settings.GITHUB_APP_PRIVATE_KEY.encode(), password=None
        )
        signature = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, TypeError, AttributeError) as error:
        raise GitHubError("The server's GitHub App key is invalid.") from error
    return f"{message}.{encoded(signature)}"


def request_payload(method: str, url: str, token: str = "", **kwargs: object) -> object:
    try:
        response = httpx.request(
            method,
            url,
            headers={
                "Accept": "application/json",
                **({"Authorization": f"Bearer {token}"} if token else {}),
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise GitHubError(
            "GitHub access failed. Check the installation and permissions."
        ) from error
    return result


def request_json(method: str, url: str, token: str = "", **kwargs: object) -> dict:
    result = request_payload(method, url, token, **kwargs)
    if not isinstance(result, dict) or "error" in result:
        raise GitHubError("GitHub returned an invalid authorization response.")
    return result


def repository_branches(installation_id: str, repository_id: int) -> list[str]:
    metadata = repository_metadata(installation_id, repository_id)
    token = installation_token(installation_id, repository_id)
    branches = []
    for page in range(1, 101):
        batch = request_payload(
            "GET",
            f"{API_URL}/repos/{metadata['full_name']}/branches?per_page=100&page={page}",
            token,
        )
        if not isinstance(batch, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("name"), str)
            for item in batch
        ):
            raise GitHubError("GitHub returned an invalid branch listing.")
        branches.extend(item["name"] for item in batch)
        if len(batch) < 100:
            return branches
    raise GitHubError("This repository has too many branches to list.")


def installation_token(
    installation_id: str,
    repository_id: int | None = None,
    permissions: dict[str, str] | None = None,
) -> str:
    """A short-lived token. Read-only unless the caller names other permissions."""
    if not installation_id.isdecimal():
        raise GitHubError("Invalid installation identity.")
    payload: dict[str, object] = {"permissions": permissions or {"contents": "read"}}
    if repository_id is not None:
        payload["repository_ids"] = [repository_id]
    data = request_json(
        "POST",
        f"{API_URL}/app/installations/{installation_id}/access_tokens",
        app_token(),
        json=payload,
    )
    return data["token"]


def installation_repositories(installation_id: str) -> list[dict]:
    token = installation_token(installation_id)
    repositories = []
    for page in range(1, 101):
        data = request_json(
            "GET",
            f"{API_URL}/installation/repositories?per_page=100&page={page}",
            token,
        )
        batch = data.get("repositories", [])
        repositories.extend(batch)
        if len(batch) < 100:
            return repositories
    raise GitHubError("Installation has too many repositories to list.")


def repository_metadata(installation_id: str, repository_id: int) -> dict:
    token = installation_token(installation_id, repository_id)
    data = request_json("GET", f"{API_URL}/repositories/{repository_id}", token)
    if data.get("id") != repository_id:
        raise GitHubError("GitHub repository identity changed.")
    return data


def branch_head(installation_id: str, repository_id: int, branch: str) -> str:
    token = installation_token(installation_id, repository_id)
    data = request_json(
        "GET",
        f"{API_URL}/repositories/{repository_id}/commits/{quote(branch, safe='')}",
        token,
    )
    return data["sha"]
