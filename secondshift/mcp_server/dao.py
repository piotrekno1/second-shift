"""Queries and writes on MCP tool call records."""

from datetime import datetime

from secondshift.mcp_server.models import ToolCall
from secondshift.workspaces.models import ApiToken


def create_tool_call(token: ApiToken, tool: str, outcome: ToolCall.Outcome) -> ToolCall:
    """Record one tool call made with this token."""
    return ToolCall.objects.create(
        workspace_id=token.workspace_id,
        token=token,
        user_id=token.created_by_id,
        tool=tool,
        outcome=outcome,
    )


def delete_tool_calls_before(cutoff: datetime) -> int:
    """Delete tool calls made before the cutoff. Returns how many."""
    deleted, _ = ToolCall.objects.filter(created_at__lt=cutoff).delete()
    return deleted
