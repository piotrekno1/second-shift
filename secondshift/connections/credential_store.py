"""Where a connection's secret comes from.

Code that sends a credential reads it here, never from the model fields. Today
every secret is an encrypted field on the connection row. A store for an
external secret manager can be added behind the same two functions.

Writing a secret and OAuth refresh still set the fields directly.
"""

from enum import StrEnum
from typing import Protocol

from secondshift.connections.models import Connection


class Secret(StrEnum):
    """A secret a connection can hold, named by the field that stores it."""

    TOKEN = "access_token"
    DELIVERY_URL = "delivery_url"


class CredentialStore(Protocol):
    def has(self, connection: Connection, secret: Secret) -> bool:
        """Whether the secret is set, without fetching it if that costs a call."""
        ...

    def read(self, connection: Connection, secret: Secret) -> str:
        """The secret value. Callers keep it in memory and never log it."""
        ...


class DatabaseStore:
    """Secrets kept encrypted on the connection row."""

    def has(self, connection: Connection, secret: Secret) -> bool:
        return bool(getattr(connection, secret.value))

    def read(self, connection: Connection, secret: Secret) -> str:
        return str(getattr(connection, secret.value))


DATABASE_STORE = DatabaseStore()


def store_for(connection: Connection) -> CredentialStore:
    return DATABASE_STORE


def has_secret(connection: Connection, secret: Secret) -> bool:
    return store_for(connection).has(connection, secret)


def read_secret(connection: Connection, secret: Secret) -> str:
    return store_for(connection).read(connection, secret)
