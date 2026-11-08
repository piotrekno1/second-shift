import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, unquote

from django.conf import settings

from secondshift.connections.models import Connection
from secondshift.jobs.dao import approved_notification_targets
from secondshift.jobs.models import Job
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
# A Markdown link to a file the agent saved, such as [report](/outputs/a.csv).
OUTPUT_LINK = re.compile(r"\[([^\]\n]+)\]\(/outputs/([^)\s]+)\)")


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
    return approved_notification_targets(run.job_id, run.workspace_id, planned)


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
        "issues": issue_links(run),
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


def has_issues(run: Run) -> bool:
    return bool((run.execution_plan or {}).get("issues"))


def issue_links(run: Run) -> list[dict[str, Any]] | None:
    """The run's issues on GitHub, opened now or already open. None if not asked."""
    if not has_issues(run):
        return None
    return [
        {
            "number": issue.number,
            "title": issue.title,
            "url": issue.url,
            "already_open": issue.already_open,
        }
        for issue in run.issues.exclude(number__isnull=True)
    ]


def slack_text(text: str) -> str:
    """Escape the characters Slack reads as markup, so a title stays text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def issues_line(run: Run) -> str:
    """Each issue's link, or why none opened, for runs that asked for issues."""
    if not has_issues(run):
        return ""
    delivery = run.deliveries.filter(kind=RunDelivery.Kind.ISSUES).first()
    if delivery is None or delivery.status in {
        RunDelivery.Status.PENDING,
        RunDelivery.Status.RETRYING,
    }:
        return ""
    if delivery.status == RunDelivery.Status.SKIPPED:
        return f"No issues opened. {delivery.message}".strip()
    lines = [
        delivery.message
        if delivery.status == RunDelivery.Status.DELIVERED
        else f"Some issues could not open. {delivery.message}".strip()
    ]
    for issue in run.issues.exclude(number__isnull=True):
        note = " (already open)" if issue.already_open else ""
        lines.append(f"• <{issue.url}|#{issue.number} {slack_text(issue.title)}>{note}")
    return "\n".join(lines)


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


def link_saved_files(run: Run, text: str) -> str:
    """Point links to saved files at their download. Opening one needs a sign-in."""
    saved = {item["name"] for item in (run.outputs or {}).get("files", [])}
    base = f"{settings.FRONTEND_URL.rstrip('/')}/api/runs/{run.pk}/saved-files/"

    def replace(match: re.Match[str]) -> str:
        name = unquote(match.group(2))
        if name not in saved:
            return match.group(0)
        # | and > would end Slack's link syntax early.
        label = re.sub(r"[|<>]", "", match.group(1)).strip() or name
        return f"<{base}{quote(name)}|{label}>"

    return OUTPUT_LINK.sub(replace, text)


def run_report_text(run: Run, run_url: str) -> str:
    """Status first, then the summary, then the link, with blank lines between."""
    status = STATUS_LABELS.get(run.status, run.status)
    mark = STATUS_MARKS.get(run.status, "•")
    summary = link_saved_files(run, run.summary.strip()) or "No summary."
    lines = [
        f"{mark} {run.display_name} {status} · {run.repository.full_name}",
        summary,
    ]
    if outputs := outputs_line(run):
        lines.append(outputs)
    if pull_request := pull_request_line(run):
        lines.append(pull_request)
    if issues := issues_line(run):
        lines.append(issues)
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
