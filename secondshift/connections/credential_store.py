"""Where a connection's secret comes from.

Code that sends a credential reads it here, never from the model fields. Most
secrets are encrypted fields on the connection row. A connection with the
reference auth strategy keeps its one secret in an external manager instead,
and the secret is fetched on every use.

Writing a secret and OAuth refresh still set the fields directly.
"""

from enum import StrEnum
from typing import Protocol

from secondshift.connections.errors import CredentialUnavailable
from secondshift.connections.models import Connection
from secondshift.connections.vault import (
    NOT_CONFIGURED,
    REFERENCE_HINT,
    is_vault_reference,
    parse_reference,
    read_vault_secret,
    vault_configured,
)


class Secret(StrEnum):
    """A secret a connection can hold, named by the field that stores it."""

    TOKEN = "access_token"
    DELIVERY_URL = "delivery_url"


class CredentialStore(Protocol):
    def has(self, connection: Connection, secret: Secret) -> bool:
        """Whether the secret is set, without fetching it if that costs a call."""
        ...

    def read(self, connection: Connection, secret: Secret) -> str:
        """The secret value. Callers keep it in memory and never log it.

        Raises CredentialUnavailable when the secret is set but cannot be read.
        """
        ...


class DatabaseStore:
    """Secrets kept encrypted on the connection row."""

    def has(self, connection: Connection, secret: Secret) -> bool:
        return bool(getattr(connection, secret.value))

    def read(self, connection: Connection, secret: Secret) -> str:
        return str(getattr(connection, secret.value))


def referenced_secret(connection: Connection) -> Secret:
    """The one secret a reference stands for."""
    if connection.provider == Connection.Provider.WEBHOOK:
        return Secret.DELIVERY_URL
    return Secret.TOKEN


class VaultStore:
    """One secret kept in HashiCorp Vault, read on every use."""

    def has(self, connection: Connection, secret: Secret) -> bool:
        return secret == referenced_secret(connection)

    def read(self, connection: Connection, secret: Secret) -> str:
        if not self.has(connection, secret):
            return ""
        return read_vault_secret(
            connection.workspace.slug, connection.credential_reference
        )


class UnknownStore:
    """A reference no store understands. Fails closed."""

    def has(self, connection: Connection, secret: Secret) -> bool:
        return secret == referenced_secret(connection)

    def read(self, connection: Connection, secret: Secret) -> str:
        raise CredentialUnavailable("This connection's secret reference is invalid.")


DATABASE_STORE = DatabaseStore()
VAULT_STORE = VaultStore()
UNKNOWN_STORE = UnknownStore()


def store_for(connection: Connection) -> CredentialStore:
    if connection.auth_strategy != Connection.AuthStrategy.REFERENCE:
        return DATABASE_STORE
    if is_vault_reference(connection.credential_reference):
        return VAULT_STORE
    return UNKNOWN_STORE


def checked_reference(reference: str) -> str:
    """A reference some store understands, in its normal form. Not fetched."""
    reference = reference.strip()
    if not is_vault_reference(reference):
        raise CredentialUnavailable(REFERENCE_HINT)
    if not vault_configured():
        raise CredentialUnavailable(NOT_CONFIGURED)
    return str(parse_reference(reference))


def has_secret(connection: Connection, secret: Secret) -> bool:
    return store_for(connection).has(connection, secret)


def read_secret(connection: Connection, secret: Secret) -> str:
    return store_for(connection).read(connection, secret)
