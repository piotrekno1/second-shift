import base64
import json
import time
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from secondshift.github import app as github_app
from secondshift.github.errors import GitHubError


@pytest.fixture
def app_settings(settings: Any) -> Any:
    settings.GITHUB_APP_ID = "123"
    settings.GITHUB_APP_SLUG = "secondshift-test"
    settings.GITHUB_APP_CLIENT_ID = "client"
    settings.GITHUB_APP_CLIENT_SECRET = "secret"
    settings.GITHUB_APP_PRIVATE_KEY = "configured-key"
    return settings


def test_app_jwt_is_signed_and_short_lived(app_settings: Any) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app_settings.GITHUB_APP_PRIVATE_KEY = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    header, payload, signature = github_app.app_token().split(".")

    def decode(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    data = json.loads(decode(payload))
    assert data["iss"] == "123"
    assert data["iat"] < time.time() < data["exp"] < time.time() + 600
    key.public_key().verify(
        decode(signature),
        f"{header}.{payload}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_installation_token_is_repository_scoped(monkeypatch: Any) -> None:
    request = Mock(return_value={"token": "installation-secret"})
    monkeypatch.setattr(github_app, "request_json", request)
    monkeypatch.setattr(github_app, "app_token", lambda: "jwt")
    assert github_app.installation_token("42", 7) == "installation-secret"
    assert request.call_args.kwargs["json"] == {
        "permissions": {"contents": "read"},
        "repository_ids": [7],
    }


def test_github_transport_errors_are_sanitized(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        github_app.httpx,
        "request",
        Mock(side_effect=httpx.ConnectError("private-secret")),
    )
    with pytest.raises(GitHubError) as caught:
        github_app.request_json("GET", "https://api.github.com/user", "private-secret")
    assert "private-secret" not in str(caught.value)


def test_oauth_exchange_does_not_send_an_empty_bearer_token(monkeypatch: Any) -> None:
    request = Mock(return_value=Mock(json=lambda: {"access_token": "user-secret"}))
    monkeypatch.setattr(github_app.httpx, "request", request)
    github_app.request_json(
        "POST", github_app.OAUTH_URL + "/access_token", json={"code": "code"}
    )
    assert "Authorization" not in request.call_args.kwargs["headers"]


def test_branch_listing_is_scoped_to_selected_repository(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        github_app, "repository_metadata", lambda *args: {"full_name": "owner/project"}
    )
    token = Mock(return_value="installation-secret")
    monkeypatch.setattr(github_app, "installation_token", token)
    request = Mock(
        side_effect=[
            [{"name": f"branch-{index}"} for index in range(100)],
            [{"name": "main"}],
        ]
    )
    monkeypatch.setattr(github_app, "request_payload", request)
    branches = github_app.repository_branches("42", 7)
    assert len(branches) == 101
    assert branches[-1] == "main"
    token.assert_called_once_with("42", 7)
    assert "page=2" in request.call_args.args[1]
