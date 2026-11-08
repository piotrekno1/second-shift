"""Audit records of MCP tool calls, kept as long as runs."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from secondshift.mcp_server.dao import create_tool_call, delete_tool_calls_before
from secondshift.mcp_server.models import TOOL_NAME_MAX, ToolCall
from secondshift.workspaces.models import ApiToken


def record_call(token: ApiToken, tool: str, outcome: ToolCall.Outcome) -> None:
    create_tool_call(token, tool[:TOOL_NAME_MAX], outcome)


def prune_tool_calls(days: int | None = None) -> int:
    """Delete tool calls older than the run retention window."""
    days = settings.RUN_RETENTION_DAYS if days is None else days
    if days <= 0:
        return 0
    return delete_tool_calls_before(timezone.now() - timedelta(days=days))
