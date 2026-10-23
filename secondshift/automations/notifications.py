from typing import Any

from django.conf import settings

from secondshift.automations.models import JobGrant, Run
from secondshift.integrations.models import Connection

STATUS_LABELS = {
    Run.Status.SUCCEEDED: "succeeded",
    Run.Status.FAILED: "failed",
    Run.Status.CANCELED: "was canceled",
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
    status = STATUS_LABELS.get(run.status, run.status)
    run_url = f"{settings.FRONTEND_URL.rstrip('/')}/jobs/{job.pk}"
    summary = run.summary or "No summary."
    return {
        "event": "run.finished",
        # Slack, Mattermost and Discord (/slack URLs) render this field directly.
        "text": f"{job.name} {status} — {summary}\n{run_url}",
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
