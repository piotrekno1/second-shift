"""Server-owned GitHub App credentials and installation-scoped API access."""

import base64
import json
import time

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


def installation_token(
    installation_id: str,
    repository_id: int | None = None,
    permissions: dict[str, str] | None = None,
) -> str:
    """A token that expires within an hour. Read-only unless permissions say more."""
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


def installation_permissions(installation_id: str) -> dict[str, str]:
    """What the installation's owner granted the App, such as {"actions": "read"}."""
    if not installation_id.isdecimal():
        raise GitHubError("Invalid installation identity.")
    data = request_json(
        "GET", f"{GITHUB_API_URL}/app/installations/{installation_id}", app_token()
    )
    permissions = data.get("permissions")
    return permissions if isinstance(permissions, dict) else {}
