import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.connections.credential_store import Secret, has_secret, read_secret
from secondshift.connections.errors import CredentialUnavailable
from secondshift.connections.model_providers import (
    ModelProviderError,
    create_model_connection,
    update_model_connection,
)
from secondshift.connections.models import Connection
from secondshift.connections.vault import parse_reference, read_vault_secret
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace

VAULT_TOKEN = "hvs.server-token"
KEY = "sk-ant-from-vault"
SAVED_KEY = "sk-ant-saved-in-database"


def vault_answers(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        "secondshift.connections.vault.vault_client",
        lambda: httpx.Client(transport=httpx.MockTransport(record)),
    )
    return calls


def kv(fields: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json={"data": {"data": fields}})


def status(code: int) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(code, json={"errors": [VAULT_TOKEN]})


@pytest.fixture
def vault(settings: Any) -> Any:
    settings.VAULT_ADDR = "https://vault.internal:8200"
    settings.VAULT_TOKEN = VAULT_TOKEN
    settings.VAULT_NAMESPACE = ""
    settings.VAULT_MOUNT = "secret"
    settings.VAULT_PATH_PREFIX = "secondshift/{workspace}"
    return settings


@pytest.fixture
def owner(db: object) -> tuple[AbstractUser, Workspace]:
    user = get_user_model().objects.create_user(username="owner")
    return user, create_workspace(name="Test", slug="team-a", owner=user)


@pytest.mark.parametrize(
    ("reference", "normal"),
    [
        ("vault:anthropic#api_key", "vault:anthropic#api_key"),
        ("vault:/models/anthropic/#api_key", "vault:models/anthropic#api_key"),
    ],
)
def test_references_are_normalized(reference: str, normal: str) -> None:
    assert str(parse_reference(reference)) == normal


@pytest.mark.parametrize(
    "reference",
    [
        "vault:anthropic",
        "vault:#api_key",
        "vault:../team-b/anthropic#api_key",
        "vault:models/./anthropic#api_key",
        "vault:models//anthropic#api_key",
        "vault:anthropic#",
        "vault:anthropic#a/b",
        "op://vault/item/field",
    ],
)
def test_references_that_leave_the_prefix_are_refused(reference: str) -> None:
    with pytest.raises(CredentialUnavailable):
        parse_reference(reference)


def test_reads_below_the_workspace_prefix_with_the_server_token(
    vault: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault.VAULT_NAMESPACE = "engineering"
    calls = vault_answers(monkeypatch, kv({"api_key": KEY}))

    assert read_vault_secret("team-a", "vault:models/anthropic#api_key") == KEY

    sent = calls[0]
    assert str(sent.url) == (
        "https://vault.internal:8200/v1/secret/data/secondshift/team-a/models/anthropic"
    )
    assert sent.headers["X-Vault-Token"] == VAULT_TOKEN
    assert sent.headers["X-Vault-Namespace"] == "engineering"


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (status(404), "Vault has no secret at vault:anthropic#api_key."),
        (status(403), "Vault refused to read vault:anthropic#api_key."),
        (status(503), "Vault answered HTTP 503 for vault:anthropic#api_key."),
        (kv({"other": KEY}), "Vault's secret at anthropic has no field api_key."),
        (kv({"api_key": ""}), "Vault's vault:anthropic#api_key is empty or not text."),
        (kv({"api_key": 5}), "Vault's vault:anthropic#api_key is empty or not text."),
    ],
)
def test_vault_problems_fail_closed_with_a_clear_message(
    vault: Any,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    message: str,
) -> None:
    vault_answers(monkeypatch, handler)

    with pytest.raises(CredentialUnavailable) as raised:
        read_vault_secret("team-a", "vault:anthropic#api_key")

    assert str(raised.value) == message
    assert VAULT_TOKEN not in str(raised.value)


def test_unreachable_vault_fails_closed(
    vault: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    vault_answers(monkeypatch, refuse)

    with pytest.raises(CredentialUnavailable, match="could not be reached"):
        read_vault_secret("team-a", "vault:anthropic#api_key")


def test_unconfigured_vault_fails_closed(
    vault: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault.VAULT_ADDR = ""
    calls = vault_answers(monkeypatch, kv({"api_key": KEY}))

    with pytest.raises(CredentialUnavailable, match="not set up"):
        read_vault_secret("team-a", "vault:anthropic#api_key")
    assert calls == []


@pytest.mark.django_db
def test_a_referenced_key_is_never_saved_and_is_read_on_every_use(
    vault: Any, monkeypatch: pytest.MonkeyPatch, owner: tuple[AbstractUser, Workspace]
) -> None:
    _, workspace = owner
    answers = iter([KEY, "sk-ant-rotated"])
    vault_answers(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"data": {"data": {"api_key": next(answers)}}}
        ),
    )

    connection = create_model_connection(
        workspace, "anthropic", "", "", credential_reference=" vault:anthropic#api_key "
    )
    connection.refresh_from_db()

    assert connection.auth_strategy == Connection.AuthStrategy.REFERENCE
    assert connection.credential_reference == "vault:anthropic#api_key"
    assert connection.access_token == ""
    assert has_secret(connection, Secret.TOKEN)
    assert not has_secret(connection, Secret.DELIVERY_URL)
    assert read_secret(connection, Secret.TOKEN) == KEY
    assert read_secret(connection, Secret.TOKEN) == "sk-ant-rotated"
    assert read_secret(connection, Secret.DELIVERY_URL) == ""


@pytest.mark.django_db
def test_a_key_and_a_reference_replace_each_other(
    vault: Any, owner: tuple[AbstractUser, Workspace]
) -> None:
    _, workspace = owner
    connection = create_model_connection(workspace, "anthropic", "", SAVED_KEY)

    update_model_connection(connection, credential_reference="vault:anthropic#api_key")
    connection.refresh_from_db()
    assert connection.auth_strategy == Connection.AuthStrategy.REFERENCE
    assert connection.access_token == ""

    update_model_connection(connection, api_key=SAVED_KEY)
    connection.refresh_from_db()
    assert connection.auth_strategy == Connection.AuthStrategy.API_KEY
    assert connection.credential_reference == ""
    assert connection.access_token == SAVED_KEY


@pytest.mark.django_db
def test_references_are_refused_when_they_cannot_work(
    vault: Any, owner: tuple[AbstractUser, Workspace]
) -> None:
    _, workspace = owner

    with pytest.raises(ModelProviderError, match="not both"):
        create_model_connection(
            workspace,
            "anthropic",
            "",
            SAVED_KEY,
            credential_reference="vault:anthropic#api_key",
        )
    with pytest.raises(ModelProviderError, match="vault:<path>#<field>"):
        create_model_connection(
            workspace, "anthropic", "", "", credential_reference="vault:../x#key"
        )
    vault.VAULT_ADDR = ""
    with pytest.raises(ModelProviderError, match="not set up"):
        create_model_connection(
            workspace, "anthropic", "", "", credential_reference="vault:a#key"
        )


@pytest.mark.django_db
def test_api_returns_the_reference_and_checks_through_vault(
    client: Client,
    vault: Any,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    user, _ = owner
    client.force_login(user)
    vault_answers(monkeypatch, status(403))

    created = client.post(
        "/api/connections/model",
        data=json.dumps(
            {"provider": "anthropic", "credential_reference": "vault:anthropic#k"}
        ),
        content_type="application/json",
    )
    listed = client.post(
        f"/api/connections/{created.json()['id']}/model/models",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert created.status_code == 200
    assert created.json()["credential_reference"] == "vault:anthropic#k"
    assert listed.status_code == 422
    assert listed.json()["detail"] == "Vault refused to read vault:anthropic#k."
