"""Per-run model usage totals for listings."""

from django.db.models import Count, F, Q, QuerySet, Sum

from secondshift.runs.models import Run


def with_model_usage(runs: QuerySet[Run]) -> QuerySet[Run]:
    return runs.annotate(
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
