from django.db import models

from secondshift.connections.models import Connection
from secondshift.runs.models import Run


class RunToken(models.Model):
    """A per-run credential for the model gateway. Only its SHA-256 is stored."""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="tokens")
    connection = models.ForeignKey(
        Connection,
        on_delete=models.CASCADE,
        related_name="run_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    budget_tokens = models.PositiveBigIntegerField()
    used_tokens = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Run {self.run_id} gateway token"


class ModelUsage(models.Model):
    """One gateway request: what was called and what it cost in tokens."""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="model_usage")
    connection = models.ForeignKey(
        Connection,
        on_delete=models.SET_NULL,
        related_name="model_usage",
        null=True,
    )
    provider = models.CharField(max_length=24)
    model = models.CharField(max_length=200)
    status_code = models.PositiveSmallIntegerField()
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cache_read_tokens = models.PositiveBigIntegerField(default=0)
    cache_write_tokens = models.PositiveBigIntegerField(default=0)
    # USD at list price; null when the model has no known price.
    cost_usd = models.DecimalField(
        max_digits=14, decimal_places=8, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Run {self.run_id}: {self.model} ({self.status_code})"
