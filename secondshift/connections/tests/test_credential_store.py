import pytest
from django.test import RequestFactory

from secondshift.connections import credential_store
from secondshift.connections.credential_store import (
    DatabaseStore,
    Secret,
    has_secret,
    read_secret,
)
from secondshift.connections.model_providers import ModelFamily
from secondshift.connections.models import Connection
from secondshift.connections.webhooks import post_webhook
from secondshift.gateway.proxy import upstream_headers

STORED = "sk-stored-in-database"
EXTERNAL = "sk-from-another-store"


class FakeStore:
    """Answers for every connection, so a test sees which store a caller used."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.reads: list[Secret] = []

    def has(self, connection: Connection, secret: Secret) -> bool:
        return bool(self.value)

    def read(self, connection: Connection, secret: Secret) -> str:
        self.reads.append(secret)
        return self.value


def model_connection(token: str = STORED) -> Connection:
    return Connection(
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        access_token=token,
    )


def test_database_store_reads_the_encrypted_fields() -> None:
    connection = Connection(access_token=STORED, delivery_url="")
    store = DatabaseStore()

    assert store.has(connection, Secret.TOKEN)
    assert store.read(connection, Secret.TOKEN) == STORED
    assert not store.has(connection, Secret.DELIVERY_URL)
    assert store.read(connection, Secret.DELIVERY_URL) == ""


def test_connections_use_the_database_store_by_default() -> None:
    connection = model_connection()

    assert has_secret(connection, Secret.TOKEN)
    assert read_secret(connection, Secret.TOKEN) == STORED


def test_gateway_sends_the_secret_from_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore(EXTERNAL)
    monkeypatch.setattr(credential_store, "store_for", lambda connection: store)

    headers = upstream_headers(
        RequestFactory().post("/"), ModelFamily.ANTHROPIC, model_connection()
    )

    assert headers["x-api-key"] == EXTERNAL
    assert store.reads == [Secret.TOKEN]


def test_gateway_reads_an_optional_key_once(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore(EXTERNAL)
    monkeypatch.setattr(credential_store, "store_for", lambda connection: store)

    headers = upstream_headers(
        RequestFactory().post("/"), ModelFamily.OPENAI, model_connection()
    )

    assert headers["Authorization"] == f"Bearer {EXTERNAL}"
    assert store.reads == [Secret.TOKEN]


def test_webhook_without_a_stored_url_is_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore("")
    monkeypatch.setattr(credential_store, "store_for", lambda connection: store)
    connection = Connection(
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        delivery_url="https://hooks.example.com/stored",
    )

    delivery = post_webhook(connection, {"text": "hello"})

    assert not delivery.ok
    assert store.reads == []
