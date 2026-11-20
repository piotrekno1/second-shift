"""Hand back the issue a finished run's agent named.

SecondShift comments on the issue and swaps its labels. It reads only what
the run stored, so a retry needs no agent or sandbox. The token is limited to
this repository and to issues. It never reaches the sandbox or the model.
"""

import logging

from secondshift.connections.github import shows_attribution
from secondshift.github import credentials
from secondshift.github.errors import GitHubError
from secondshift.github.issues import (
    add_issue_labels,
    comment_on_issue,
    issue_state,
    remove_issue_label,
)
from secondshift.jobs.execution_plan import ApprovedIssuesPlan
from secondshift.repositories.github.access import github_access
from secondshift.repositories.models import Repository
from secondshift.runs.dao import (
    create_delivery,
    finish_delivery,
    get_run,
    run_delivery,
    run_hand_back,
)
from secondshift.runs.issue_delivery import write_access_problem
from secondshift.runs.links import attribution_footer
from secondshift.runs.models import Run, RunDelivery, RunHandBack

logger = logging.getLogger(__name__)


def hand_back_delivery(run: Run) -> RunDelivery | None:
    return run_delivery(run, RunDelivery.Kind.HAND_BACK)


def skip_reason(run: Run, repository: Repository) -> str:
    """Why this run hands nothing back, or an empty string if it should."""
    if repository.source_type != Repository.SourceType.GITHUB:
        return "Local repositories have no issues to hand back."
    if not repository.allow_issues:
        return "An owner has not allowed issues for this repository."
    if run.status != Run.Status.SUCCEEDED:
        return "The run did not succeed."
    return ""


def comment_body(run: Run, hand_back: RunHandBack) -> str:
    """The agent's comment, and a footer when the connection shows attribution."""
    if not shows_attribution(run.repository.github_connection):
        return hand_back.comment
    footer = attribution_footer("Handed back", run)
    return f"{hand_back.comment}\n\n---\n\n{footer}"


def labels_message(number: int, approved: ApprovedIssuesPlan) -> str:
    message = f"Handed #{number} back. Removed {approved.label}"
    if approved.hand_back:
        message += f" and added {approved.hand_back}"
    return f"{message}."


def hand_back_run_issue(run_id: int) -> None:
    run = get_run(
        run_id,
        "job__repository__github_connection",
        "task__repository__github_connection",
    )
    hand_back = run_hand_back(run.pk)
    approved = run.plan.approved_issues
    if hand_back is None or approved is None:
        # Most runs fix their issue and hand nothing back.
        return
    delivery = hand_back_delivery(run) or create_delivery(
        run, RunDelivery.Kind.HAND_BACK, f"Issue #{hand_back.number}"
    )
    if delivery.status == RunDelivery.Status.DELIVERED:
        return
    repository = run.repository
    if reason := skip_reason(run, repository):
        finish_delivery(delivery, RunDelivery.Status.SKIPPED, reason)
        return
    delivery.attempts += 1
    try:
        access = github_access(repository, credentials.ISSUES_WRITE)
    except GitHubError as error:
        finish_delivery(
            delivery,
            RunDelivery.Status.FAILED,
            write_access_problem(repository, error),
        )
        return
    try:
        acted = apply_hand_back(
            access.token,
            access.full_name,
            hand_back,
            approved,
            comment_body(run, hand_back),
        )
    except GitHubError as error:
        # These messages never contain the token.
        finish_delivery(delivery, RunDelivery.Status.FAILED, str(error))
        return
    except Exception:
        logger.exception("handing back an issue for run %s failed", run_id)
        finish_delivery(
            delivery,
            RunDelivery.Status.FAILED,
            "Handing the issue back failed unexpectedly.",
        )
        return
    if not acted:
        finish_delivery(
            delivery,
            RunDelivery.Status.SKIPPED,
            f"#{hand_back.number} is not an open issue with {approved.label}, "
            "so it was left alone.",
        )
        return
    delivery.external_url = hand_back.comment_url
    finish_delivery(
        delivery,
        RunDelivery.Status.DELIVERED,
        labels_message(hand_back.number, approved),
    )


def apply_hand_back(
    token: str,
    full_name: str,
    hand_back: RunHandBack,
    approved: ApprovedIssuesPlan,
    comment: str,
) -> bool:
    """Comment once and swap the labels. False when the issue isn't approved.

    The agent names the issue, so its labels decide what SecondShift may
    touch: only an open issue a maintainer approved for this job. Once the
    comment is posted, a retry finishes the labels whatever they are now.
    """
    number = hand_back.number
    if not hand_back.comment_url:
        state = issue_state(token, full_name, number)
        if (
            state is None
            or state.pull_request
            or not state.open
            or approved.label.casefold()
            not in {label.casefold() for label in state.labels}
        ):
            return False
        hand_back.comment_url = comment_on_issue(token, full_name, number, comment)
        hand_back.save(update_fields=["comment_url"])
    remove_issue_label(token, full_name, number, approved.label)
    if approved.hand_back:
        add_issue_labels(token, full_name, number, [approved.hand_back])
    return True
