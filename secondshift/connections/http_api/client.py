"""Call one approved operation. The model supplies values, never the request shape."""

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import anyio
import httpx
from django.conf import settings
from django.utils import timezone

from secondshift.connections.http_api.spec import BODY, APIError, APIRequestError
from secondshift.connections.mcp.transport import MCP_RESPONSE_MAX_BYTES, MCPTransport
from secondshift.connections.models import Connection

BEARER = "bearer"
HEADER = "header"
AUTH_TYPES = (BEARER, HEADER)
HEADER_NAME_PATTERN = r"[A-Za-z0-9-]{1,64}"
# Headers an owner may not use for the credential: they change how the request
# is routed or framed.
RESERVED_HEADERS = {
    "host",
    "content-length",
    "content-type",
    "transfer-encoding",
    "connection",
    "cookie",
    "accept",
    "accept-encoding",
}
SCALAR_TYPES = (str, int, float, bool)
TEXT_MAX_CHARS = 64 * 1024


@dataclass(frozen=True)
class APIRequest:
    method: str
    url: str
    body: Any = None


def auth_header(settings_: dict[str, str], token: str) -> dict[str, str]:
    if settings_.get("auth") == HEADER:
        name = settings_.get("header", "")
        if (
            not re.fullmatch(HEADER_NAME_PATTERN, name)
            or name.lower() in RESERVED_HEADERS
        ):
            raise APIError("The credential header name is not allowed.")
        return {name: token}
    return {"Authorization": f"Bearer {token}"}


def path_value(value: Any) -> str:
    """One path segment. Slashes, dots-only segments, and URLs are refused."""
    if isinstance(value, bool) or not isinstance(value, SCALAR_TYPES):
        raise APIError("Path values must be text or numbers.")
    text = str(value)
    if not text or text in {".", ".."} or "/" in text or "\\" in text or "://" in text:
        raise APIError("A path value is not allowed.")
    return quote(text, safe="")


def query_values(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    if any(item is None or not isinstance(item, SCALAR_TYPES) for item in values):
        raise APIError("Query values must be text, numbers, or lists of them.")
    return [
        str(item).lower() if isinstance(item, bool) else str(item) for item in values
    ]


def build_request(
    base_url: str, operation: dict[str, Any], arguments: dict[str, Any]
) -> APIRequest:
    declared = {item["name"]: item for item in operation["parameters"]}
    unknown = set(arguments) - set(declared) - ({BODY} if operation["body"] else set())
    if unknown:
        raise APIError("The call has arguments the operation does not declare.")
    path = operation["path"]
    for name, item in declared.items():
        if item["in"] != "path":
            continue
        if name not in arguments:
            raise APIError("A required path value is missing.")
        path = path.replace("{" + name + "}", path_value(arguments[name]))
    query: list[tuple[str, str]] = []
    for name, item in declared.items():
        if item["in"] == "query" and arguments.get(name) is not None:
            query += [(name, value) for value in query_values(arguments[name])]
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    return APIRequest(
        method=operation["method"].upper(),
        url=url,
        body=arguments.get(BODY) if operation["body"] else None,
    )


def validate_api_connection(connection: Connection) -> None:
    saved = connection.metadata.get("base_url", "")
    if (
        connection.kind != Connection.Kind.API
        or connection.provider != Connection.Provider.API
        or connection.status != Connection.Status.CONNECTED
        or not connection.access_token
        or not saved
        or connection.endpoint_url != saved
        or (connection.expires_at and connection.expires_at <= timezone.now())
    ):
        raise APIError("This API connection is unavailable.")


async def send(connection: Connection, request: APIRequest) -> dict[str, Any]:
    """Status and body. No token, exception text, or redirect target leaks."""
    headers = {
        **auth_header(connection.metadata.get("settings", {}), connection.access_token),
        "Accept": "application/json, text/plain;q=0.5",
        "Accept-Encoding": "identity",
    }
    try:
        with anyio.fail_after(settings.OUTBOUND_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=MCPTransport(),
                headers=headers,
                timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(
                    request.method,
                    request.url,
                    json=request.body if request.body is not None else None,
                )
                raw = await response.aread()
    except Exception:
        raise APIRequestError(
            "The API request failed. Check the service and token."
        ) from None
    if 300 <= response.status_code < 400:
        return {
            "status": response.status_code,
            "ok": False,
            "body": "The API redirected. SecondShift does not follow redirects.",
        }
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    text = raw.decode("utf-8", errors="replace").replace(
        connection.access_token, "[redacted]"
    )
    body: Any
    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            body = json.loads(text)
        except ValueError:
            body = text[:TEXT_MAX_CHARS]
    elif content_type.startswith("text/") or not content_type:
        body = text[:TEXT_MAX_CHARS]
    else:
        body = f"Unsupported content type: {content_type[:80]}"
    result = {"status": response.status_code, "ok": response.is_success, "body": body}
    if len(json.dumps(result).encode()) > MCP_RESPONSE_MAX_BYTES:
        raise APIError("The API response exceeded the size limit.")
    return result
