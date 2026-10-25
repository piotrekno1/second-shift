"""Create, edit, and re-check HTTP API connections."""

import re
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from secondshift.integrations.http_api.client import (
    AUTH_TYPES,
    BEARER,
    HEADER,
    HEADER_NAME_PATTERN,
    RESERVED_HEADERS,
)
from secondshift.integrations.http_api.spec import APIError, Spec, load_spec
from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import (
    OutboundUrlError,
    host_label,
    validate_outbound_url,
)
from secondshift.integrations.services import ConnectionConflict
from secondshift.workspaces.models import Workspace

TOKEN_PATTERN = r"\S{1,2000}"
# Connection.endpoint_url is a URLField with Django's default limit.
BASE_URL_MAX_LENGTH = 200


@dataclass(frozen=True)
class SpecSummary:
    title: str
    servers: tuple[str, ...]
    operations: int
    read_only: int


def summarize(spec_url: str, spec_text: str) -> SpecSummary:
    spec = load_spec(spec_url, spec_text)
    return SpecSummary(
        title=spec.title,
        servers=spec.servers,
        operations=len(spec.operations),
        read_only=sum(1 for item in spec.operations.values() if item.read_only),
    )


def clean_base_url(value: str) -> str:
    """Where requests and the credential go. Checked with DNS, like webhooks."""
    value = value.strip().rstrip("/")
    if not value or len(value) > BASE_URL_MAX_LENGTH:
        raise APIError("Enter the API base URL.")
    try:
        value = validate_outbound_url(value, "base URL")
    except OutboundUrlError as error:
        raise APIError(str(error)) from None
    if "?" in value or "#" in value:
        raise APIError("The base URL cannot have a query or fragment.")
    return value


def clean_auth(auth: str, header: str) -> dict[str, str]:
    if auth not in AUTH_TYPES:
        raise APIError("Choose how to send the credential.")
    if auth == BEARER:
        return {"auth": BEARER}
    if (
        not re.fullmatch(HEADER_NAME_PATTERN, header)
        or header.lower() in RESERVED_HEADERS
    ):
        raise APIError("Enter a header name such as X-API-Key.")
    return {"auth": HEADER, "header": header}


def offered(spec: Spec) -> dict[str, Any]:
    return {name: operation.definition() for name, operation in spec.operations.items()}


def select_operations(
    connection: Connection, operations: dict[str, Any], selected: list[str] | None
) -> None:
    """GET operations start selected. Anything else waits for the owner."""
    if selected is None:
        chosen = {name for name, item in operations.items() if item["readOnly"]}
    else:
        if set(selected) - set(operations):
            raise APIError("Choose operations from the spec.")
        chosen = set(selected)
    connection.available_tools = sorted(chosen)
    connection.metadata = {
        **connection.metadata,
        "tools": {name: operations[name] for name in sorted(chosen)},
        "offered_tools": operations,
    }


def create_api_connection(
    workspace: Workspace,
    binding_key: str,
    display_name: str,
    spec_url: str,
    spec_text: str,
    base_url: str,
    auth: str,
    header: str,
    token: str,
) -> Connection:
    if not re.fullmatch(TOKEN_PATTERN, token):
        raise APIError("Enter the credential without spaces.")
    if Connection.objects.filter(workspace=workspace, binding_key=binding_key).exists():
        raise ConnectionConflict("This binding already has a connection.")
    # Cheap checks first, before downloading the spec.
    auth_settings = clean_auth(auth, header)
    base_url = clean_base_url(base_url)
    spec = load_spec(spec_url, spec_text)
    connection = Connection(
        workspace=workspace,
        provider=Connection.Provider.API,
        kind=Connection.Kind.API,
        binding_key=binding_key,
        display_name=display_name.strip() or spec.title or host_label(base_url),
        endpoint_url=base_url,
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token=token,
        metadata={
            "target": host_label(base_url),
            "base_url": base_url,
            "spec_url": spec_url,
            "title": spec.title,
            "settings": auth_settings,
        },
    )
    select_operations(connection, offered(spec), None)
    connection.full_clean()
    try:
        with transaction.atomic():
            connection.save()
    except IntegrityError:
        raise ConnectionConflict("This binding already has a connection.") from None
    return connection


def update_api_connection(
    connection: Connection,
    display_name: str | None,
    token: str | None,
    base_url: str | None,
    operations: list[str] | None,
) -> Connection:
    """A blank token keeps the current one. A new base URL is an explicit choice."""
    if connection.kind != Connection.Kind.API:
        raise ConnectionConflict("Only API connections can be edited here.")
    if token:
        if not re.fullmatch(TOKEN_PATTERN, token):
            raise APIError("Enter the credential without spaces.")
        connection.access_token = token
    elif (
        connection.status != Connection.Status.CONNECTED or not connection.access_token
    ):
        raise APIError("Enter a new credential to reconnect.")
    if display_name is not None:
        connection.display_name = display_name.strip() or connection.binding_key
    if base_url is not None:
        cleaned = clean_base_url(base_url)
        connection.endpoint_url = cleaned
        connection.metadata = {
            **connection.metadata,
            "base_url": cleaned,
            "target": host_label(cleaned),
        }
    if operations is not None:
        select_operations(
            connection, connection.metadata.get("offered_tools", {}), operations
        )
    connection.status = Connection.Status.CONNECTED
    connection.full_clean()
    connection.save()
    return connection


def recheck_api_connection(connection: Connection) -> str:
    """Load the spec again. The saved base URL never changes here."""
    spec_url = connection.metadata.get("spec_url", "")
    if not spec_url:
        return (
            "This connection uses a pasted spec, so there is nothing to download again."
        )
    spec = load_spec(spec_url, "")
    operations = offered(spec)
    kept = [name for name in connection.available_tools if name in operations]
    select_operations(connection, operations, kept)
    connection.save(update_fields=["available_tools", "metadata", "updated_at"])
    message = f"Found {len(operations)} operations. {len(kept)} enabled."
    base_url = connection.metadata.get("base_url", "")
    if spec.servers and base_url not in spec.servers:
        message += f" The spec now lists a different server. Requests still go to {host_label(base_url)}."
    return message
