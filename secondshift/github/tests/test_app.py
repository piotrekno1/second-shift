import base64
import json
import time
from typing import Any
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from secondshift.github import app as github_app


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


def test_installation_settings_must_be_on_github(monkeypatch: Any) -> None:
    monkeypatch.setattr(github_app, "app_token", lambda: "jwt")
    monkeypatch.setattr(
        github_app,
        "request_json",
        lambda *args: {
            "permissions": {"contents": "read"},
            "html_url": "https://evil.example/settings",
        },
    )
    details = github_app.installation("42")
    assert details.permissions == {"contents": "read"}
    assert details.settings_url == ""
