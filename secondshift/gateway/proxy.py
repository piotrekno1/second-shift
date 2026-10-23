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
from typing import Any

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse

from secondshift.gateway.metering import StreamMeter, record_usage, usage_from_json
from secondshift.gateway.models import RunToken
from secondshift.gateway.tokens import aauthenticate, request_token
from secondshift.integrations.model_providers import base_url, provider_family
from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import (
    OutboundUrlError,
    PinnedTransport,
    validate_outbound_url,
)

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
# Paths a run may call, relative to each family's /v1 root. Only the billed
# ones are metered.
METERED_PATHS = {
    "anthropic": {"messages"},
    "openai": {"chat/completions", "responses"},
}
ALLOWED_PATHS = {
    "anthropic": {"messages", "messages/count_tokens"},
    "openai": {"chat/completions", "responses"},
}
FORWARDED_REQUEST_HEADERS = {
    "anthropic": ("anthropic-version", "anthropic-beta"),
    "openai": (),
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


def error_response(family: str, status: int, kind: str, message: str) -> HttpResponse:
    """An error in the provider's own shape, so agent SDKs surface it cleanly."""
    if family == "anthropic":
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
    request: HttpRequest, family: str, connection: Connection
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
        "User-Agent": "SecondShift-Gateway/1",
    }
    for name in FORWARDED_REQUEST_HEADERS[family]:
        if value := request.headers.get(name):
            headers[name] = value
    if family == "anthropic":
        headers.setdefault("anthropic-version", ANTHROPIC_VERSION)
        headers["x-api-key"] = connection.access_token
    elif connection.access_token:
        headers["Authorization"] = f"Bearer {connection.access_token}"
    return headers


def copy_headers(upstream: httpx.Response, response: HttpResponse) -> None:
    for name in FORWARDED_RESPONSE_HEADERS:
        if value := upstream.headers.get(name):
            response[name] = value


async def forward(request: HttpRequest, family: str, path: str) -> HttpResponse:
    if family not in ALLOWED_PATHS:
        return error_response("openai", 404, "not_found_error", "Unknown API family.")
    run_token = await aauthenticate(request_token(request))
    if run_token is None:
        return error_response(
            family, 401, "authentication_error", "Invalid or expired run token."
        )
    connection = run_token.connection
    if provider_family(connection) != family:
        return error_response(
            family,
            404,
            "not_found_error",
            "This run's model provider does not serve this API.",
        )
    if request.method != "POST" or path not in ALLOWED_PATHS[family]:
        return error_response(
            family, 404, "not_found_error", "This endpoint is not available."
        )

    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    # The ASGI handler has already spooled the body; read it off the event loop.
    raw = (
        b""
        if declared > settings.GATEWAY_MAX_BODY_BYTES
        else await sync_to_async(request.read, thread_sensitive=False)(
            settings.GATEWAY_MAX_BODY_BYTES + 1
        )
    )
    if declared > settings.GATEWAY_MAX_BODY_BYTES or (
        len(raw) > settings.GATEWAY_MAX_BODY_BYTES
    ):
        return error_response(
            family, 413, "request_too_large", "The request body is too large."
        )
    try:
        payload = json.loads(raw)
    except ValueError:
        return error_response(
            family, 400, "invalid_request_error", "The body must be JSON."
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
        return error_response(
            family, 400, "invalid_request_error", "The request must name a model."
        )
    model = payload["model"]
    if connection.allowed_models and model not in connection.allowed_models:
        return error_response(
            family,
            403,
            "permission_error",
            f"Model {model} is not allowed for this workspace.",
        )
    metered = path in METERED_PATHS[family]
    if metered and run_token.used_tokens >= run_token.budget_tokens:
        return error_response(
            family,
            429,
            "rate_limit_error",
            "This run has used its token budget.",
        )
    if family == "openai" and path == "chat/completions" and payload.get("stream"):
        # Without this, streamed chat completions never report usage.
        options = payload.get("stream_options")
        payload["stream_options"] = {
            **(options if isinstance(options, dict) else {}),
            "include_usage": True,
        }
        raw = json.dumps(payload).encode()

    root = base_url(connection)
    try:
        # Scheme and userinfo rules; the transport re-checks addresses on connect.
        validate_outbound_url(root, "provider URL", resolve=False)
    except OutboundUrlError as error:
        return error_response(family, 502, "api_error", str(error))
    url = f"{root}/v1/{path}" if family == "anthropic" else f"{root}/{path}"
    client = upstream_client()
    try:
        upstream = await client.send(
            client.build_request(
                "POST",
                url,
                content=raw,
                headers=upstream_headers(request, family, connection),
            ),
            stream=True,
        )
    except httpx.TimeoutException:
        return error_response(family, 504, "api_error", "The provider timed out.")
    except httpx.HTTPError as error:
        logger.warning(
            "gateway request for run %s failed: %s",
            run_token.run_id,
            type(error).__name__,
        )
        return error_response(
            family, 502, "api_error", "The provider could not be reached."
        )

    content_type = upstream.headers.get("content-type", "application/json")
    if content_type.startswith("text/event-stream"):
        response: HttpResponse = StreamingHttpResponse(
            relay_stream(upstream, run_token, family, model, metered),
            status=upstream.status_code,
            content_type=content_type,
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
    else:
        try:
            content = await upstream.aread()
        except httpx.HTTPError:
            return error_response(
                family, 502, "api_error", "The provider response was interrupted."
            )
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
        response = HttpResponse(
            content, status=upstream.status_code, content_type=content_type
        )
    copy_headers(upstream, response)
    return response


async def relay_stream(
    upstream: httpx.Response,
    run_token: RunToken,
    family: str,
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
    family: str,
    model: str,
    metered: bool,
    meter: StreamMeter,
) -> None:
    await upstream.aclose()
    if metered:
        await record_usage(run_token, family, model, upstream.status_code, meter.usage)
