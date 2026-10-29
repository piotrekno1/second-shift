"""The /mcp endpoint: token auth, rate limit, audit, then protocol dispatch."""

import logging

from django.http import HttpRequest, HttpResponse
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
from secondshift.mcp_server.rate_limit import wait_seconds
from secondshift.mcp_server.tools_registry import McpCall
from secondshift.workspaces.models import ApiToken
from secondshift.workspaces.tokens import ApiTokenAuth

logger = logging.getLogger(__name__)

api = McpAPI(
    title="SecondShift MCP",
    urls_namespace="mcp",
    auth=ApiTokenAuth(),
    docs_url=None,
    openapi_url=None,
)


def tool_name(payload: McpRequest) -> str:
    name = payload.params.get("name")
    return name if isinstance(name, str) else ""


def rate_limited(payload: McpRequest, wait: int) -> HttpResponse:
    response = render_mcp_result(
        McpHttpResult.error(
            payload.id,
            JsonRpcErrorCode.SERVER_ERROR,
            f"Too many tool calls. Try again in {wait} seconds.",
            status=429,
        )
    )
    response["Retry-After"] = str(wait)
    return response


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
    name = tool_name(payload)
    if wait := wait_seconds(token.created_by_id):
        record_call(token, name, ToolCall.Outcome.RATE_LIMITED)
        return rate_limited(payload, wait)
    result = dispatch(request, payload)
    record_call(token, name, outcome(result))
    return render_mcp_result(result)
