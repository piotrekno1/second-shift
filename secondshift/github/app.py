"""Server-owned GitHub App credentials and installation-scoped API access."""

import base64
import json
import time
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings

from secondshift.config.constants import GITHUB_API_URL
from secondshift.github.errors import GitHubError
from secondshift.github.rest import paginated, request_json


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


def repository_branches(installation_id: str, repository_id: int) -> list[str]:
    metadata = repository_metadata(installation_id, repository_id)
    token = installation_token(installation_id, repository_id)
    batch = paginated(
        f"{GITHUB_API_URL}/repos/{metadata['full_name']}/branches",
        token,
        None,
        "This repository has too many branches to list.",
    )
    if any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str)
        for item in batch
    ):
        raise GitHubError("GitHub returned an invalid branch listing.")
    return [item["name"] for item in batch]


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
        f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens",
        app_token(),
        json=payload,
    )
    return data["token"]


def installation_repositories(installation_id: str) -> list[dict]:
    return paginated(
        f"{GITHUB_API_URL}/installation/repositories",
        installation_token(installation_id),
        "repositories",
        "Installation has too many repositories to list.",
    )


def repository_metadata(installation_id: str, repository_id: int) -> dict:
    token = installation_token(installation_id, repository_id)
    data = request_json("GET", f"{GITHUB_API_URL}/repositories/{repository_id}", token)
    if data.get("id") != repository_id:
        raise GitHubError("GitHub repository identity changed.")
    return data


def branch_head(installation_id: str, repository_id: int, branch: str) -> str:
    token = installation_token(installation_id, repository_id)
    data = request_json(
        "GET",
        f"{GITHUB_API_URL}/repositories/{repository_id}/commits/{quote(branch, safe='')}",
        token,
    )
    return data["sha"]
