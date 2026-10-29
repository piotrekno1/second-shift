"""The /mcp endpoint: token auth, a rate limit, protocol dispatch and audit."""

import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from ninja.throttling import UserRateThrottle
from thin_mcp import (
    JsonRpcErrorCode,
    McpHttpResult,
    McpMethodName,
    McpRequest,
    McpRequestHeaders,
)
from thin_mcp.django_ninja import McpAPI, render_mcp_result

from secondshift.mcp_server.audit import record_call
from secondshift.mcp_server.core import server
from secondshift.mcp_server.models import ToolCall
from secondshift.mcp_server.tools_registry import McpCall
from secondshift.workspaces.models import ApiToken
from secondshift.workspaces.tokens import ApiTokenAuth

logger = logging.getLogger(__name__)

# Counts requests per person, across all their tokens. It runs after token
# auth, which signs the request in as the token's creator, and keeps its
# counts in the shared cache.
throttle = UserRateThrottle(f"{settings.MCP_RATE_LIMIT}/m")

api = McpAPI(
    title="SecondShift MCP",
    urls_namespace="mcp",
    auth=ApiTokenAuth(),
    throttle=throttle,
    docs_url=None,
    openapi_url=None,
)


def tool_name(payload: McpRequest) -> str:
    name = payload.params.get("name")
    return name if isinstance(name, str) else ""


def dispatch(request: HttpRequest, payload: McpRequest) -> McpHttpResult:
    try:
        return server.dispatch_request(
            user=request.user, request=payload, resources=(McpCall(request),)
        )
    except Exception:
        logger.exception("Unhandled MCP request error")
        if payload.is_notification:
            return McpHttpResult.accepted()
        return McpHttpResult.error(
            payload.id, JsonRpcErrorCode.INTERNAL_ERROR, "Internal error.", status=500
        )


def outcome(result: McpHttpResult) -> ToolCall.Outcome:
    body = result.body.model_dump(by_alias=True) if result.body else {}
    tool_result = body.get("result") or {}
    if "error" in body or tool_result.get("isError"):
        return ToolCall.Outcome.ERROR
    return ToolCall.Outcome.OK


@api.post("")
def mcp_endpoint(request: HttpRequest, payload: McpRequest) -> HttpResponse:
    token: ApiToken = request.auth
    headers = McpRequestHeaders.from_mapping(request.headers)
    invalid = server.validate_http_request(payload, headers)
    if invalid is not None:
        return render_mcp_result(invalid)
    if payload.method != McpMethodName.TOOLS_CALL:
        return render_mcp_result(dispatch(request, payload))
    result = dispatch(request, payload)
    record_call(token, tool_name(payload), outcome(result))
    return render_mcp_result(result)
