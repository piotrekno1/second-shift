"""Audit records of MCP tool calls, kept as long as runs."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from secondshift.mcp_server.models import TOOL_NAME_MAX, ToolCall
from secondshift.workspaces.models import ApiToken


def record_call(token: ApiToken, tool: str, outcome: ToolCall.Outcome) -> None:
    ToolCall.objects.create(
        workspace_id=token.workspace_id,
        token=token,
        user_id=token.created_by_id,
        tool=tool[:TOOL_NAME_MAX],
        outcome=outcome,
    )


def prune_tool_calls(days: int | None = None) -> int:
    """Delete tool calls older than the run retention window."""
    days = settings.RUN_RETENTION_DAYS if days is None else days
    if days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = ToolCall.objects.filter(created_at__lt=cutoff).delete()
    return deleted
