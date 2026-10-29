from django.conf import settings
from django.db import models

from secondshift.workspaces.models import ApiToken, Workspace

# Longer tool names are cut, so a client cannot fill the table with one call.
TOOL_NAME_MAX = 128


class ToolCall(models.Model):
    """One MCP tool call. Arguments and results are never recorded."""

    class Outcome(models.TextChoices):
        OK = "ok", "Succeeded"
        ERROR = "error", "Failed"
        RATE_LIMITED = "rate_limited", "Rate limited"

    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="tool_calls"
    )
    token = models.ForeignKey(
        ApiToken,
        on_delete=models.SET_NULL,
        null=True,
        related_name="tool_calls",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    tool = models.CharField(max_length=TOOL_NAME_MAX)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.tool} ({self.get_outcome_display()})"
