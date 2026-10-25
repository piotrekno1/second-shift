"""How each connection is used: by which jobs, and for model providers, at what cost."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from secondshift.automations.models import JobGrant
from secondshift.gateway.models import ModelUsage
from secondshift.workspaces.models import Workspace

WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class ConnectionUsage:
    # Jobs with approved access through this connection.
    jobs: int = 0
    runs_7d: int = 0
    spend_7d_usd: Decimal = Decimal(0)
    # Some model calls had no known price, so spend is a lower bound.
    spend_partial: bool = False
    # Last model call; services don't record individual calls.
    last_used_at: datetime | None = None


def connection_usage(
    workspace: Workspace, now: datetime | None = None
) -> dict[int, ConnectionUsage]:
    start = (now or timezone.now()) - WINDOW
    jobs = {
        row["connection_id"]: row["jobs"]
        for row in JobGrant.objects.filter(
            connection__workspace=workspace, status=JobGrant.Status.APPROVED
        )
        .values("connection_id")
        .annotate(jobs=Count("capability_request__job", distinct=True))
        .order_by()
    }
    recent = Q(created_at__gte=start)
    models = {
        row["connection_id"]: row
        for row in ModelUsage.objects.filter(connection__workspace=workspace)
        .values("connection_id")
        .annotate(
            runs=Count("run_id", distinct=True, filter=recent),
            spend=Sum("cost_usd", filter=recent),
            unpriced=Count("pk", filter=recent & Q(cost_usd__isnull=True)),
            last=Max("created_at"),
        )
        .order_by()
    }
    usage: dict[int, ConnectionUsage] = {}
    for connection_id in jobs.keys() | models.keys():
        row = models.get(connection_id, {})
        usage[connection_id] = ConnectionUsage(
            jobs=jobs.get(connection_id, 0),
            runs_7d=row.get("runs", 0),
            spend_7d_usd=row.get("spend") or Decimal(0),
            spend_partial=row.get("unpriced", 0) > 0,
            last_used_at=row.get("last"),
        )
    return usage
