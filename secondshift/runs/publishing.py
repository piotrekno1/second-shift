"""Publish a finished run's changes as a draft pull request.

Publishing reads only what the run stored: the pinned commit, the collected
patch and the report. A retry repeats this without the agent or a sandbox.

When the GitHub connection shows attribution, the branch, the description and
the commit's trailers name SecondShift. When it does not, nothing does: the
branch is named after the title and the description is the report alone.
"""

import logging
import re
import tempfile
from pathlib import Path

from django.utils.text import slugify

from secondshift.connections.github import shows_attribution
from secondshift.github.pulls import MAX_BODY_CHARS
from secondshift.repositories.models import Repository
from secondshift.repositories.pull_requests import (
    DraftPullRequest,
    PublishError,
    publish_pull_request,
)
from secondshift.runner_protocol.sandbox import MAX_CHANGES_BYTES, SANDBOX_WORKSPACE_DIR
from secondshift.runs.artifacts import artifact_storage, read_text
from secondshift.runs.dao import (
    create_delivery,
    finish_delivery,
    get_run,
    run_delivery,
    run_hand_back,
)
from secondshift.runs.models import Run, RunDelivery
from secondshift.runs.reporting.collection import plural

logger = logging.getLogger(__name__)

SKIP_CI = "[skip ci]"
# A merged draft would otherwise look checked. Nothing in it names SecondShift.
CI_WAITS_NOTE = (
    "CI has not run on this commit. It waits for a review: push a commit to run it."
)
BRANCH_PREFIX = "secondshift/run-"
# Long enough to read, short enough for a branch list.
BRANCH_SLUG_CHARS = 40


def record_pending_pull_request(run: Run) -> None:
    """Shown as pending from the start, and kept if publishing fails."""
    plan = run.plan.pull_request
    if plan:
        create_delivery(
            run,
            RunDelivery.Kind.PULL_REQUEST,
            f"{run.repository.full_name} into {plan.base}",
        )


def pull_request_delivery(run: Run) -> RunDelivery | None:
    return run_delivery(run, RunDelivery.Kind.PULL_REQUEST)


def skip_reason(run: Run, repository: Repository) -> str:
    """Why this run opens no pull request, or an empty string if it should."""
    if repository.source_type != Repository.SourceType.GITHUB:
        return "Local repositories cannot open pull requests."
    if not repository.allow_pull_requests:
        return "An owner has not allowed pull requests for this repository."
    if run.status != Run.Status.SUCCEEDED:
        return "The run did not succeed."
    if run.collected is None:
        return "The run's changes could not be collected."
    changes = run.collected.changes
    if changes.too_large:
        return (
            f"The changes were larger than {MAX_CHANGES_BYTES // 2**20} MiB, "
            "so they were not kept."
        )
    if not changes.path:
        return "The agent made no changes."
    return ""


def handed_back_instead(run: Run) -> bool:
    """The agent handed an issue back and changed nothing."""
    if run.status != Run.Status.SUCCEEDED or run.collected is None:
        return False
    changes = run.collected.changes
    return (
        not changes.path and not changes.too_large and run_hand_back(run.pk) is not None
    )


MAX_TITLE_CHARS = 200
# SecondShift's own GitHub account. GitHub lists a commit's co-authors among
# a repository's contributors.
CO_AUTHOR = "SecondShift <359045448+secondshift-bot@users.noreply.github.com>"
# A Markdown link to a file in the sandbox, such as [a.py](/workspace/a.py:3).
SANDBOX_LINK = re.compile(
    rf"\[([^\]\n]*)\]\({re.escape(SANDBOX_WORKSPACE_DIR)}/([^)\s]+)\)"
)


def draft_for(run: Run) -> DraftPullRequest:
    if run.finished_at is None:
        raise PublishError("The run has not finished.")
    plan = run.plan.pull_request
    if plan is None:
        raise PublishError("The run did not ask for a pull request.")
    report = ""
    if run.output_path:
        report, _ = read_text(run.output_path, MAX_BODY_CHARS)
    title, report = title_and_report(report, run.display_name)
    report = repository_paths(report)
    attribution = shows_attribution(run.repository.github_connection)
    body = report or run.summary
    paragraphs = [title]
    if not plan.run_ci:
        # CI waits for a person, because the patch has not been reviewed yet.
        paragraphs.append(SKIP_CI)
        body = f"{body}\n\n{CI_WAITS_NOTE}"
    if attribution:
        body = f"{body}\n\n---\n\n{footer(run)}"
        paragraphs.append(trailers(run))
    return DraftPullRequest(
        branch=branch_name(run, title, attribution),
        base=plan.base,
        title=title,
        body=body,
        message="\n\n".join(paragraphs),
        # The same date on every attempt gives the same commit.
        committed_at=run.finished_at,
    )


def repository_paths(report: str) -> str:
    """Links to sandbox files as repository paths, such as `a.py:3`.

    Agents link the files they changed by their path in the sandbox. On
    GitHub such a link leads nowhere, and the diff shows the files anyway.
    Link text other than the file's name stays in front of the path.
    """

    def path(match: re.Match[str]) -> str:
        text, target = match[1].strip(), match[2]
        name = target.split(":")[0].rsplit("/", 1)[-1]
        return f"`{target}`" if text in ("", name, target) else f"{text} (`{target}`)"

    return SANDBOX_LINK.sub(path, report)


def footer(run: Run) -> str:
    return f"Opened by SecondShift from run #{run.pk} at {run.commit_sha[:12]}."


def trailers(run: Run) -> str:
    """Git trailers, so tools can find the run behind a commit.

    The co-author credits SecondShift's account, as Claude Code credits Claude.
    """
    lines = [f"SecondShift-Run: {run.pk}"]
    if run.job is not None:
        lines.append(f"SecondShift-Job: {run.job.name}")
    lines.append(f"Co-authored-by: {CO_AUTHOR}")
    return "\n".join(lines)


def title_and_report(report: str, fallback: str) -> tuple[str, str]:
    """The pull request's title, and the report without it.

    A report that starts with a `# ` heading names its pull request, so a
    job that fixes a different issue each run gets a title for each. Other
    reports keep the job's name.
    """
    first, _, rest = report.strip().partition("\n")
    if first.startswith("# ") and (heading := first[2:].strip()):
        return heading[:MAX_TITLE_CHARS], rest.strip()
    return fallback[:MAX_TITLE_CHARS], report.strip()


def branch_name(run: Run, title: str, attribution: bool) -> str:
    """The same name on every attempt, so a retry pushes the same branch."""
    if attribution:
        return f"{BRANCH_PREFIX}{run.pk}"
    slug = slugify(title)[:BRANCH_SLUG_CHARS].strip("-") or "changes"
    return f"{slug}-{run.pk}"


def publish_run(run_id: int) -> None:
    run = get_run(run_id, "job", "task__repository__github_connection")
    delivery = pull_request_delivery(run)
    if delivery is None or delivery.status == RunDelivery.Status.DELIVERED:
        return
    if reason := skip_reason(run, run.repository):
        if handed_back_instead(run):
            # The hand-back row says what happened. A skipped draft adds noise.
            delivery.delete()
            return
        finish_delivery(delivery, RunDelivery.Status.SKIPPED, reason)
        return
    delivery.attempts += 1
    try:
        with tempfile.TemporaryDirectory(prefix="secondshift-patch-") as folder:
            patch = Path(folder) / "changes.patch"
            with artifact_storage().open(run.collected.changes.path, "rb") as src:
                patch.write_bytes(src.read())
            opened = publish_pull_request(
                run.repository, run.commit_sha, patch, draft_for(run)
            )
    except PublishError as error:
        finish_delivery(delivery, RunDelivery.Status.FAILED, str(error))
        return
    except Exception:
        logger.exception("publishing run %s failed", run_id)
        finish_delivery(
            delivery, RunDelivery.Status.FAILED, "Publishing failed unexpectedly."
        )
        return
    delivery.external_url = opened.url
    count = run.collected.changes.file_count
    finish_delivery(
        delivery,
        RunDelivery.Status.DELIVERED,
        f"Opened draft #{opened.number} with changes to {plural(count, 'file')}.",
    )
