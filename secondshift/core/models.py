from django.db import models


class RunLimitOverrides(models.Model):
    """Run limits an owner chose. Empty means the next level up decides.

    A job falls back to its workspace, and a workspace to the server defaults.
    The server maximums cap every value (see secondshift.automations.limits).
    """

    max_turns = models.PositiveIntegerField(null=True, blank=True)
    max_tool_calls = models.PositiveIntegerField(null=True, blank=True)
    token_budget = models.PositiveBigIntegerField(null=True, blank=True)
    timeout_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        abstract = True
