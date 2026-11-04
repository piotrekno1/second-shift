"""Open the issues a finished run's agent proposed.

This reads only what the run stored, so a retry needs no agent or sandbox.
The token is limited to this repository and to issues. It never reaches the
sandbox or the model.
"""

import logging

from secondshift.connections.models import Connection
from secondshift.github import credentials
from secondshift.github.errors import GitHubError
from secondshift.github.issues import open_issue, open_issue_titled
from secondshift.repositories.github_access import github_access
from secondshift.repositories.models import Repository
from secondshift.runs.execution.issues import issue_limit
from secondshift.runs.models import Run, RunDelivery, RunEvent, RunIssue
from secondshift.runs.reporting.collection import plural

logger = logging.getLogger(__name__)

APP_WRITE_HINT = (
    "GitHub refused to open issues. Give the GitHub App Issues write "
    "permission, then retry."
)


def record_pending_issues(run: Run) -> None:
    """Shown as pending from the start, and kept if opening fails."""
    if issue_limit(run.execution_plan):
        RunDelivery.objects.create(
            run=run,
            kind=RunDelivery.Kind.ISSUES,
            target=f"Issues in {run.repository.full_name}",
        )


def issues_delivery(run: Run) -> RunDelivery | None:
    return run.deliveries.filter(kind=RunDelivery.Kind.ISSUES).first()


def skip_reason(run: Run, repository: Repository) -> str:
    """Why this run opens no issues, or an empty string if it should."""
    if repository.source_type != Repository.SourceType.GITHUB:
        return "Local repositories cannot receive issues."
    if not repository.allow_issues:
        return "An owner has not allowed issues for this repository."
    if run.status != Run.Status.SUCCEEDED:
        return "The run did not succeed."
    if not run.issues.exists():
        return "The agent proposed no issues."
    return ""


def issue_body(run: Run, issue: RunIssue) -> str:
    footer = f"Opened by SecondShift from run #{run.pk} at {run.commit_sha[:12]}."
    return f"{issue.body}\n\n---\n\n{footer}" if issue.body else footer


def open_run_issues(run_id: int) -> None:
    run = Run.objects.select_related(
        "job__repository__github_connection", "task__repository__github_connection"
    ).get(pk=run_id)
    delivery = issues_delivery(run)
    if delivery is None or delivery.status == RunDelivery.Status.DELIVERED:
        return
    repository = run.repository
    if reason := skip_reason(run, repository):
        finish(delivery, RunDelivery.Status.SKIPPED, reason)
        return
    delivery.attempts += 1
    try:
        access = github_access(repository, credentials.ISSUES_WRITE)
    except GitHubError as error:
        with_token = (
            repository.github_connection is not None
            and repository.github_connection.auth_strategy
            == Connection.AuthStrategy.API_KEY
        )
        # Token errors already say what to fix, such as an expired token.
        finish(
            delivery,
            RunDelivery.Status.FAILED,
            str(error) if with_token else APP_WRITE_HINT,
        )
        return
    try:
        # Issues opened by an earlier attempt keep their number.
        for issue in run.issues.filter(number__isnull=True):
            found = open_issue_titled(access.token, access.full_name, issue.title)
            if found is None:
                found = open_issue(
                    access.token, access.full_name, issue.title, issue_body(run, issue)
                )
            else:
                issue.already_open = True
            issue.number, issue.url = found.number, found.url
            issue.save(update_fields=["number", "url", "already_open"])
    except GitHubError as error:
        # These messages never contain the token.
        finish(delivery, RunDelivery.Status.FAILED, f"{done(run)}{error}".strip())
        return
    except Exception:
        logger.exception("opening issues for run %s failed", run_id)
        finish(
            delivery,
            RunDelivery.Status.FAILED,
            f"{done(run)}Opening issues failed unexpectedly.".strip(),
        )
        return
    finish(delivery, RunDelivery.Status.DELIVERED, done(run).strip())


def done(run: Run) -> str:
    """What has happened so far, as a sentence or two followed by a space."""
    issues = list(run.issues.exclude(number__isnull=True))
    opened = sum(1 for issue in issues if not issue.already_open)
    already = len(issues) - opened
    parts = []
    if opened:
        parts.append(f"Opened {plural(opened, 'issue')}.")
    if already:
        verb = "was" if already == 1 else "were"
        parts.append(f"{plural(already, 'issue')} {verb} already open.")
    return " ".join(parts) + " " if parts else ""


def finish(delivery: RunDelivery, status: str, message: str) -> None:
    delivery.status = status
    delivery.message = message
    delivery.save(update_fields=["status", "message", "attempts", "updated_at"])
    RunEvent.objects.create(
        run_id=delivery.run_id,
        level="warning" if status == RunDelivery.Status.FAILED else "info",
        message=f"Issues: {message}",
        metadata={"delivery_id": delivery.pk},
    )
