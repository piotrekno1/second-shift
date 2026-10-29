from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings

from secondshift.connections.models import Connection
from secondshift.jobs.models import Job, JobGrant
from secondshift.repositories.models import Repository
from secondshift.runs.models import Run

STATUS_LABELS = {
    Run.Status.SUCCEEDED: "succeeded",
    Run.Status.FAILED: "failed",
    Run.Status.CANCELED: "was canceled",
}
# Plain emoji, so the marker shows the same in Slack, Mattermost and Discord.
STATUS_MARKS = {
    Run.Status.SUCCEEDED: "✅",
    Run.Status.FAILED: "❌",
    Run.Status.CANCELED: "⏹️",
}


def notification_targets(run: Run) -> list[int]:
    """Connections authorized when the run was planned and still approved now."""
    planned = {
        item["connection_id"]
        for item in run.execution_plan.get("capabilities", [])
        if item.get("kind") == "notification"
        and item.get("authorized")
        and item.get("connection_id")
    }
    if not planned:
        return []
    # A grant revoked or a connection disconnected after planning stops delivery.
    return sorted(
        JobGrant.objects.filter(
            capability_request__job_id=run.job_id,
            capability_request__kind="notification",
            status=JobGrant.Status.APPROVED,
            connection_id__in=planned,
            connection__status=Connection.Status.CONNECTED,
            connection__workspace_id=run.job.workspace_id,
        ).values_list("connection_id", flat=True)
    )


def run_report_payload(run: Run) -> dict[str, Any]:
    job = run.job
    run_url = f"{settings.FRONTEND_URL.rstrip('/')}/runs/{run.pk}"
    return {
        "event": "run.finished",
        # Slack, Mattermost and Discord (/slack URLs) render this field directly.
        "text": run_report_text(run, run_url),
        "run": {
            "id": run.pk,
            "status": run.status,
            "summary": run.summary,
            "commit_sha": run.commit_sha,
            "scheduled_for": run.scheduled_for.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "url": run_url,
        },
        "job": {"key": job.key, "name": job.name},
        "repository": {"full_name": job.repository.full_name},
    }


def run_report_text(run: Run, run_url: str) -> str:
    """Status first, then the summary, then the link, with blank lines between."""
    job = run.job
    status = STATUS_LABELS.get(run.status, run.status)
    mark = STATUS_MARKS.get(run.status, "•")
    summary = run.summary.strip() or "No summary."
    return "\n\n".join(
        (
            f"{mark} {job.name} {status} · {job.repository.full_name}",
            summary,
            # Slack link syntax. Mattermost and Discord /slack webhooks accept it.
            f"<{run_url}|Open run #{run.pk}>",
        )
    )


def sample_run_report_payload() -> dict[str, Any]:
    """What a webhook receives after a run, built from made-up records.

    It uses the same code as real deliveries, so the sample cannot drift.
    """
    finished_at = datetime(2026, 10, 26, 9, 4, tzinfo=UTC)
    repository = Repository(full_name="acme/web")
    job = Job(key="weekly-review", name="Weekly review", repository=repository)
    run = Run(
        pk=128,
        job=job,
        status=Run.Status.SUCCEEDED,
        summary="Found 2 stale setup steps in README.md. Details are in the report.",
        commit_sha="8f3c9d2" * 5 + "8f3c9",
        scheduled_for=finished_at - timedelta(minutes=4),
        finished_at=finished_at,
    )
    return run_report_payload(run)
