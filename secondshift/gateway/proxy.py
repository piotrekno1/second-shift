"""Model gateway: the only way a run reaches a model provider.

A run authenticates with a short-lived token bound to that run. The gateway
checks the path, model and token budget, injects the workspace's API key and
streams the provider's answer back, recording token usage. The run never sees
the provider key.
"""

import asyncio
import json
import logging
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse

from secondshift.config.constants import TOKEN_BUDGET_MESSAGE, ModelFamily
from secondshift.connections.model_providers import api_root, provider_family
from secondshift.connections.models import Connection
from secondshift.connections.outbound import (
    OutboundUrlError,
    PinnedTransport,
    validate_outbound_url,
)
from secondshift.gateway.metering import StreamMeter, record_usage, usage_from_json
from secondshift.gateway.models import RunToken
from secondshift.gateway.tokens import aauthenticate, request_token

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class FamilyRoutes:
    # Paths a run may call, relative to the provider's API root. Only the
    # billed ones are metered.
    allowed: frozenset[str]
    metered: frozenset[str]
    forwarded_headers: tuple[str, ...] = ()


OPENAI_PATHS = frozenset({"chat/completions", "responses"})
ROUTES = {
    ModelFamily.ANTHROPIC: FamilyRoutes(
        allowed=frozenset({"messages", "messages/count_tokens"}),
        metered=frozenset({"messages"}),
        forwarded_headers=("anthropic-version", "anthropic-beta"),
    ),
    ModelFamily.OPENAI: FamilyRoutes(allowed=OPENAI_PATHS, metered=OPENAI_PATHS),
}
FORWARDED_RESPONSE_HEADERS = (
    "request-id",
    "x-request-id",
    "retry-after",
)


# One pooled client per event loop: connections to providers are reused across
# requests, and a client is never shared between loops.
clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)


def upstream_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = clients.get(loop)
    if client is None:
        client = httpx.AsyncClient(
            transport=PinnedTransport(),
            follow_redirects=False,
            timeout=httpx.Timeout(
                settings.OUTBOUND_TIMEOUT_SECONDS,
                read=settings.GATEWAY_READ_TIMEOUT_SECONDS,
            ),
        )
        clients[loop] = client
    return client


def error_response(
    family: ModelFamily, status: int, kind: str, message: str
) -> HttpResponse:
    """An error in the provider's own shape, so agent SDKs surface it cleanly."""
    if family == ModelFamily.ANTHROPIC:
        body: dict[str, Any] = {
            "type": "error",
            "error": {"type": kind, "message": message},
        }
    else:
        body = {"error": {"message": message, "type": kind, "code": kind}}
    return HttpResponse(
        json.dumps(body), status=status, content_type="application/json"
    )


def upstream_headers(
    request: HttpRequest, family: ModelFamily, connection: Connection
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
        "User-Agent": "SecondShift-Gateway/1",
    }
    for name in ROUTES[family].forwarded_headers:
        if value := request.headers.get(name):
            headers[name] = value
    if family == ModelFamily.ANTHROPIC:
        headers.setdefault("anthropic-version", ANTHROPIC_VERSION)
        headers["x-api-key"] = connection.access_token
    elif connection.access_token:
        headers["Authorization"] = f"Bearer {connection.access_token}"
    return headers


def copy_headers(upstream: httpx.Response, response: HttpResponse) -> None:
    for name in FORWARDED_RESPONSE_HEADERS:
        if value := upstream.headers.get(name):
            response[name] = value


class GatewayRefusal(Exception):
    """A request the gateway answers itself, without calling the provider."""

    def __init__(
        self, family: ModelFamily, status: int, kind: str, message: str
    ) -> None:
        super().__init__(message)
        self.response = error_response(family, status, kind, message)


async def forward(request: HttpRequest, family_name: str, path: str) -> HttpResponse:
    if family_name not in ROUTES:
        return error_response(
            ModelFamily.OPENAI, 404, "not_found_error", "Unknown API family."
        )
    family = ModelFamily(family_name)
    try:
        run_token = await authorize(request, family, path)
        raw, payload = await read_payload(request, family)
        model, metered = check_request(run_token, family, path, payload)
        raw = with_stream_usage(family, path, payload, raw)
        upstream = await send_upstream(request, run_token, family, path, raw)
        response = await relay(upstream, run_token, family, model, metered)
    except GatewayRefusal as refusal:
        return refusal.response
    copy_headers(upstream, response)
    return response


async def authorize(request: HttpRequest, family: ModelFamily, path: str) -> RunToken:
    """The run token, if it may call this provider's endpoint."""
    run_token = await aauthenticate(request_token(request))
    if run_token is None:
        raise GatewayRefusal(
            family, 401, "authentication_error", "Invalid or expired run token."
        )
    if provider_family(run_token.connection) != family:
        raise GatewayRefusal(
            family,
            404,
            "not_found_error",
            "This run's model provider does not serve this API.",
        )
    if request.method != "POST" or path not in ROUTES[family].allowed:
        raise GatewayRefusal(
            family, 404, "not_found_error", "This endpoint is not available."
        )
    return run_token


async def read_payload(
    request: HttpRequest, family: ModelFamily
) -> tuple[bytes, dict[str, Any]]:
    """The raw body and its JSON, refused when too large or without a model."""
    limit = settings.GATEWAY_MAX_BODY_BYTES
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    # The ASGI handler has already spooled the body; read it off the event loop.
    raw = (
        b""
        if declared > limit
        else await sync_to_async(request.read, thread_sensitive=False)(limit + 1)
    )
    if declared > limit or len(raw) > limit:
        raise GatewayRefusal(
            family, 413, "request_too_large", "The request body is too large."
        )
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise GatewayRefusal(
            family, 400, "invalid_request_error", "The body must be JSON."
        ) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
        raise GatewayRefusal(
            family, 400, "invalid_request_error", "The request must name a model."
        )
    return raw, payload


def check_request(
    run_token: RunToken, family: ModelFamily, path: str, payload: dict[str, Any]
) -> tuple[str, bool]:
    """The model, and whether the call counts against the token budget."""
    model = payload["model"]
    allowed = run_token.connection.allowed_models
    if allowed and model not in allowed:
        raise GatewayRefusal(
            family,
            403,
            "permission_error",
            f"Model {model} is not allowed for this workspace.",
        )
    metered = path in ROUTES[family].metered
    if metered and run_token.used_tokens >= run_token.budget_tokens:
        refusal = GatewayRefusal(family, 429, "rate_limit_error", TOKEN_BUDGET_MESSAGE)
        # Provider SDKs retry a 429 unless told not to. Waiting never helps here.
        refusal.response["x-should-retry"] = "false"
        raise refusal
    return model, metered


def with_stream_usage(
    family: ModelFamily, path: str, payload: dict[str, Any], raw: bytes
) -> bytes:
    """Without this, streamed OpenAI chat completions never report usage."""
    if not (
        family == ModelFamily.OPENAI
        and path == "chat/completions"
        and payload.get("stream")
    ):
        return raw
    options = payload.get("stream_options")
    payload["stream_options"] = {
        **(options if isinstance(options, dict) else {}),
        "include_usage": True,
    }
    return json.dumps(payload).encode()


async def send_upstream(
    request: HttpRequest,
    run_token: RunToken,
    family: ModelFamily,
    path: str,
    raw: bytes,
) -> httpx.Response:
    connection = run_token.connection
    root = api_root(connection)
    try:
        # Scheme and userinfo rules; the transport re-checks addresses on connect.
        validate_outbound_url(root, "provider URL", resolve=False)
    except OutboundUrlError as error:
        raise GatewayRefusal(family, 502, "api_error", str(error)) from error
    url = f"{root}/{path}"
    client = upstream_client()
    try:
        return await client.send(
            client.build_request(
                "POST",
                url,
                content=raw,
                headers=upstream_headers(request, family, connection),
            ),
            stream=True,
        )
    except httpx.TimeoutException as error:
        raise GatewayRefusal(
            family, 504, "api_error", "The provider timed out."
        ) from error
    except httpx.HTTPError as error:
        logger.warning(
            "gateway request for run %s failed: %s",
            run_token.run_id,
            type(error).__name__,
        )
        raise GatewayRefusal(
            family, 502, "api_error", "The provider could not be reached."
        ) from error


async def relay(
    upstream: httpx.Response,
    run_token: RunToken,
    family: ModelFamily,
    model: str,
    metered: bool,
) -> HttpResponse:
    """Pass the provider's answer on, metering its token use."""
    content_type = upstream.headers.get("content-type", "application/json")
    if content_type.startswith("text/event-stream"):
        response = StreamingHttpResponse(
            relay_stream(upstream, run_token, family, model, metered),
            status=upstream.status_code,
            content_type=content_type,
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
    try:
        content = await upstream.aread()
    except httpx.HTTPError as error:
        raise GatewayRefusal(
            family, 502, "api_error", "The provider response was interrupted."
        ) from error
    finally:
        await upstream.aclose()
    if metered:
        try:
            body = json.loads(content)
        except ValueError:
            body = None
        await record_usage(
            run_token,
            family,
            model,
            upstream.status_code,
            usage_from_json(family, body),
        )
    return HttpResponse(content, status=upstream.status_code, content_type=content_type)


async def relay_stream(
    upstream: httpx.Response,
    run_token: RunToken,
    family: ModelFamily,
    model: str,
    metered: bool,
) -> AsyncIterator[bytes]:
    meter = StreamMeter(family)
    try:
        # aiter_bytes yields each chunk as it arrives, decompressed.
        async for chunk in upstream.aiter_bytes():
            meter.feed(chunk)
            yield chunk
    except httpx.HTTPError as error:
        logger.warning(
            "gateway stream for run %s ended early: %s",
            run_token.run_id,
            type(error).__name__,
        )
    finally:
        # Runs on completion and when the client disconnects (Django closes the
        # generator). Shielded so a second cancellation cannot lose the usage.
        await asyncio.shield(
            finish_stream(upstream, run_token, family, model, metered, meter)
        )


async def finish_stream(
    upstream: httpx.Response,
    run_token: RunToken,
    family: ModelFamily,
    model: str,
    metered: bool,
    meter: StreamMeter,
) -> None:
    await upstream.aclose()
    if metered:
        await record_usage(run_token, family, model, upstream.status_code, meter.usage)
