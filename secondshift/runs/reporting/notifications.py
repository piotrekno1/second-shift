from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings

from secondshift.connections.models import Connection
from secondshift.jobs.models import Job, JobGrant
from secondshift.repositories.models import Repository
from secondshift.runs.execution.tool_broker import standalone_connection
from secondshift.runs.models import Run, RunDelivery
from secondshift.runs.reporting.collection import plural

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
    if run.job_id is None:
        return sorted(
            connection.pk
            for capability in run.execution_plan.get("capabilities", [])
            if capability.get("kind") == Connection.Kind.NOTIFICATION
            and (connection := standalone_connection(run, capability)) is not None
        )
    # A grant revoked or a connection disconnected after planning stops delivery.
    return sorted(
        JobGrant.objects.filter(
            capability_request__job_id=run.job_id,
            capability_request__kind="notification",
            status=JobGrant.Status.APPROVED,
            connection_id__in=planned,
            connection__status=Connection.Status.CONNECTED,
            connection__workspace_id=run.workspace_id,
        ).values_list("connection_id", flat=True)
    )


def run_report_payload(run: Run) -> dict[str, Any]:
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
        "job": {
            "key": run.job.key if run.job is not None else "",
            "name": run.display_name,
        },
        "repository": {"full_name": run.repository.full_name},
        "pull_request": pull_request_link(run),
        # Names and sizes only. The files open from the run, after sign-in.
        "outputs": {
            "changed_files": changed_file_count(run),
            "saved_files": [
                {"name": item["name"], "size_bytes": item["size_bytes"]}
                for item in (run.outputs or {}).get("files", [])
            ],
        },
    }


def pull_request_link(run: Run) -> dict[str, str] | None:
    if not (run.execution_plan or {}).get("pull_request"):
        return None
    delivery = run.deliveries.filter(
        kind=RunDelivery.Kind.PULL_REQUEST, status=RunDelivery.Status.DELIVERED
    ).first()
    return {"url": delivery.external_url} if delivery else None


def pull_request_line(run: Run) -> str:
    """The draft's link, or why there is none, for runs that asked for one."""
    if not (run.execution_plan or {}).get("pull_request"):
        return ""
    delivery = run.deliveries.filter(kind=RunDelivery.Kind.PULL_REQUEST).first()
    if delivery is None:
        return ""
    if delivery.status == RunDelivery.Status.DELIVERED:
        return f"<{delivery.external_url}|Review the draft pull request>"
    if delivery.status == RunDelivery.Status.SKIPPED:
        return f"No draft pull request. {delivery.message}".strip()
    if delivery.status == RunDelivery.Status.FAILED:
        return f"The draft pull request could not open. {delivery.message}".strip()
    return ""


def changed_file_count(run: Run) -> int:
    changes = (run.outputs or {}).get("changes") or {}
    return int(changes.get("file_count", 0)) if changes.get("path") else 0


def outputs_line(run: Run) -> str:
    parts = []
    if count := changed_file_count(run):
        parts.append(plural(count, "changed file"))
    if files := (run.outputs or {}).get("files"):
        parts.append(plural(len(files), "saved file"))
    return " · ".join(parts)


def run_report_text(run: Run, run_url: str) -> str:
    """Status first, then the summary, then the link, with blank lines between."""
    status = STATUS_LABELS.get(run.status, run.status)
    mark = STATUS_MARKS.get(run.status, "•")
    summary = run.summary.strip() or "No summary."
    lines = [
        f"{mark} {run.display_name} {status} · {run.repository.full_name}",
        summary,
    ]
    if outputs := outputs_line(run):
        lines.append(outputs)
    if pull_request := pull_request_line(run):
        lines.append(pull_request)
    # Slack link syntax. Mattermost and Discord /slack webhooks accept it.
    lines.append(f"<{run_url}|Open run #{run.pk}>")
    return "\n\n".join(lines)


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
        outputs={"files": [{"name": "stale-steps.csv", "size_bytes": 182}]},
    )
    return run_report_payload(run)
