"""Per-run model usage totals for listings."""

from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery, Sum

from secondshift.config.constants import AgentBackend
from secondshift.runs.models import Run, RunEvent


def with_model_usage(runs: QuerySet[Run]) -> QuerySet[Run]:
    agent_usage = (
        RunEvent.objects.filter(
            run_id=OuterRef("pk"),
            metadata__backend__in=[AgentBackend.CLAUDE_CODE, AgentBackend.CODEX],
            metadata__has_key="usage",
        )
        .order_by("-pk")
        .values("metadata__usage")[:1]
    )
    return runs.annotate(
        agent_usage=Subquery(agent_usage),
        model_tokens=Sum(
            F("model_usage__input_tokens")
            + F("model_usage__output_tokens")
            + F("model_usage__cache_read_tokens")
            + F("model_usage__cache_write_tokens")
        ),
        # Cached prompt tokens are still input the model read.
        model_input_tokens=Sum(
            F("model_usage__input_tokens")
            + F("model_usage__cache_read_tokens")
            + F("model_usage__cache_write_tokens")
        ),
        model_output_tokens=Sum("model_usage__output_tokens"),
        model_requests=Count("model_usage"),
        model_cost_usd=Sum("model_usage__cost_usd"),
        unpriced_requests=Count(
            "model_usage", filter=Q(model_usage__cost_usd__isnull=True)
        ),
    )
