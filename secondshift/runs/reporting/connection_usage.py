"""How each connection is used: by which jobs, and for model providers, at what cost."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from secondshift.gateway.dao import usage_by_connection
from secondshift.jobs.dao import approved_jobs_per_connection
from secondshift.runs.dao import login_run_counts
from secondshift.workspaces.models import Workspace

WINDOW = timedelta(days=7)
# The window an agent login's run cap counts over.
RUN_CAP_WINDOW = timedelta(hours=24)


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
    jobs = approved_jobs_per_connection(workspace.pk)
    models = usage_by_connection(workspace.pk, start)
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


def agent_login_runs(workspace_id: int, now: datetime | None = None) -> Counter[int]:
    """Runs per agent login in the last 24 hours.

    Only runs whose plan was ready count. A run refused at its start never
    used the login.
    """
    start = (now or timezone.now()) - RUN_CAP_WINDOW
    return login_run_counts(workspace_id, start)
