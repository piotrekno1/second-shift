"""Queries and writes on gateway tokens and model usage."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db.models import Count, F, Max, Q, QuerySet, Sum
from django.utils import timezone

from secondshift.connections.models import Connection
from secondshift.gateway.models import ModelUsage, RunToken


def create_run_token(
    run_id: int,
    connection: Connection,
    token_hash: str,
    expires_at: datetime,
    budget_tokens: int,
) -> RunToken:
    """Store a run's gateway token by its hash."""
    return RunToken.objects.create(
        run_id=run_id,
        connection=connection,
        token_hash=token_hash,
        expires_at=expires_at,
        budget_tokens=budget_tokens,
    )


def revoke_run_tokens(run_id: int) -> int:
    """Revoke the run's tokens that are still live. Returns how many."""
    return RunToken.objects.filter(run_id=run_id, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def active_tokens(token_hash: str) -> QuerySet[RunToken]:
    """Live tokens with this hash, for a connected model.

    A run's tokens are revoked when it finishes, in the same transaction.
    """
    return RunToken.objects.select_related(
        "connection", "run__job", "run__task__repository"
    ).filter(
        token_hash=token_hash,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
        connection__status=Connection.Status.CONNECTED,
        connection__kind=Connection.Kind.MODEL,
    )


async def acreate_model_usage(
    run_token: RunToken,
    provider: str,
    model: str,
    status_code: int,
    tokens: dict[str, int],
    cost_usd: Decimal | None,
) -> ModelUsage:
    """Record one model request made with this token.

    `tokens` holds input_tokens, output_tokens, cache_read_tokens and
    cache_write_tokens.
    """
    return await ModelUsage.objects.acreate(
        run_id=run_token.run_id,
        connection_id=run_token.connection_id,
        provider=provider,
        model=model,
        status_code=status_code,
        cost_usd=cost_usd,
        **tokens,
    )


async def aadd_used_tokens(run_token: RunToken, tokens: int) -> None:
    """Count tokens against the token's budget."""
    await RunToken.objects.filter(pk=run_token.pk).aupdate(
        used_tokens=F("used_tokens") + tokens
    )


def usage_by_connection(
    workspace_id: int, since: datetime
) -> dict[int, dict[str, Any]]:
    """Per connection: runs, spend and unpriced requests since then, and last use."""
    recent = Q(created_at__gte=since)
    return {
        row["connection_id"]: row
        for row in ModelUsage.objects.filter(connection__workspace_id=workspace_id)
        .values("connection_id")
        .annotate(
            runs=Count("run_id", distinct=True, filter=recent),
            spend=Sum("cost_usd", filter=recent),
            unpriced=Count("pk", filter=recent & Q(cost_usd__isnull=True)),
            last=Max("created_at"),
        )
        .order_by()
    }


def spend_totals(runs: Q, start: datetime, previous_start: datetime) -> dict[str, Any]:
    """Spend since `start`, spend in the window before it, and unpriced requests.

    `runs` picks the usage by its run, with lookups that start at `run__`.
    """
    return ModelUsage.objects.filter(runs, created_at__gte=previous_start).aggregate(
        spend=Sum("cost_usd", filter=Q(created_at__gte=start)),
        previous=Sum("cost_usd", filter=Q(created_at__lt=start)),
        unpriced=Count("pk", filter=Q(created_at__gte=start, cost_usd__isnull=True)),
    )


def spend_per_job(workspace_id: int, since: datetime) -> list[dict[str, Any]]:
    """Per job: spend, runs and unpriced requests since then."""
    return list(
        ModelUsage.objects.filter(
            run__job__workspace_id=workspace_id, created_at__gte=since
        )
        .values("run__job_id")
        .annotate(
            spend=Sum("cost_usd"),
            runs=Count("run_id", distinct=True),
            unpriced=Count("pk", filter=Q(cost_usd__isnull=True)),
        )
        .order_by()
    )
