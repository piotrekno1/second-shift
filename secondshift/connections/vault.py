"""Read one secret from HashiCorp Vault's KV version 2 engine.

The server's own Vault token does the reading. A workspace names a secret by
a path relative to its own prefix, so it can never read another workspace's
secrets. Values stay in memory and are never logged.
"""

import logging
import re
from dataclasses import dataclass

import httpx
from django.conf import settings

from secondshift.connections.errors import CredentialUnavailable

logger = logging.getLogger(__name__)

SCHEME = "vault:"
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
FIELD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
REFERENCE_HINT = (
    "Use vault:<path>#<field>, for example vault:anthropic#api_key. "
    "The path is relative to the workspace's Vault prefix."
)
NOT_CONFIGURED = (
    "Vault is not set up on this server. Ask the operator to set "
    "SECONDSHIFT_VAULT_ADDR and SECONDSHIFT_VAULT_TOKEN."
)


@dataclass(frozen=True)
class VaultReference:
    path: str
    field: str

    def __str__(self) -> str:
        return f"{SCHEME}{self.path}#{self.field}"


def is_vault_reference(reference: str) -> bool:
    return reference.startswith(SCHEME)


def parse_reference(reference: str) -> VaultReference:
    """The path and field of a reference. Refuses anything that leaves the prefix."""
    if not is_vault_reference(reference):
        raise CredentialUnavailable(REFERENCE_HINT)
    path, separator, field = reference.removeprefix(SCHEME).strip().partition("#")
    segments = path.strip("/").split("/")
    if (
        not separator
        or not FIELD.fullmatch(field)
        or not all(SEGMENT.fullmatch(segment) for segment in segments)
    ):
        raise CredentialUnavailable(REFERENCE_HINT)
    return VaultReference("/".join(segments), field)


def vault_configured() -> bool:
    return bool(settings.VAULT_ADDR and settings.VAULT_TOKEN)


def secret_url(workspace_slug: str, reference: VaultReference) -> str:
    prefix = settings.VAULT_PATH_PREFIX.format(workspace=workspace_slug)
    location = "/".join(part for part in (prefix, reference.path) if part)
    return f"{settings.VAULT_ADDR}/v1/{settings.VAULT_MOUNT}/data/{location}"


def vault_client() -> httpx.Client:
    """Vault is an operator-chosen service, often on a private network."""
    return httpx.Client(timeout=settings.VAULT_TIMEOUT_SECONDS, follow_redirects=False)


def read_vault_secret(workspace_slug: str, reference: str) -> str:
    """The field's current value. Fails closed on any problem."""
    parsed = parse_reference(reference)
    if not vault_configured():
        raise CredentialUnavailable(NOT_CONFIGURED)
    headers = {"X-Vault-Token": settings.VAULT_TOKEN}
    if settings.VAULT_NAMESPACE:
        headers["X-Vault-Namespace"] = settings.VAULT_NAMESPACE
    try:
        with vault_client() as client:
            response = client.get(secret_url(workspace_slug, parsed), headers=headers)
    except httpx.HTTPError as error:
        logger.warning("Vault read for %s failed: %s", parsed, type(error).__name__)
        raise CredentialUnavailable(
            f"Vault could not be reached to read {parsed}."
        ) from None
    if response.status_code == 404:
        raise CredentialUnavailable(f"Vault has no secret at {parsed}.")
    if response.status_code == 403:
        raise CredentialUnavailable(f"Vault refused to read {parsed}.")
    if response.status_code != 200:
        raise CredentialUnavailable(
            f"Vault answered HTTP {response.status_code} for {parsed}."
        )
    try:
        value = response.json()["data"]["data"][parsed.field]
    except (ValueError, KeyError, TypeError):
        raise CredentialUnavailable(
            f"Vault's secret at {parsed.path} has no field {parsed.field}."
        ) from None
    if not isinstance(value, str) or not value:
        raise CredentialUnavailable(f"Vault's {parsed} is empty or not text.")
    return value
