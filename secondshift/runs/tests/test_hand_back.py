"""Handing an approved issue back to a person after the run."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from django.utils import timezone
from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from secondshift.connections.models import Connection
from secondshift.github.errors import GitHubError
from secondshift.github.issues import IssueState
from secondshift.jobs.manifest import ApprovedIssuesManifest
from secondshift.jobs.planner import build_execution_plan
from secondshift.repositories.github.access import GitHubAccess
from secondshift.repositories.models import Repository
from secondshift.runs import deliveries, hand_back_delivery
from secondshift.runs.constants import RETRY_HAND_BACK_WORKFLOW
from secondshift.runs.execution import hand_back
from secondshift.runs.execution.run import run_prompt
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps
from secondshift.runs.models import Run, RunDelivery, RunHandBack
from secondshift.runs.orchestration import workflows
from secondshift.runs.tests.test_publishing import github_source, queue  # noqa: F401
from secondshift.runs.tests.test_workflows import execution_run

ACCESS = GitHubAccess(42, "current/project", "ghs_token", {})
APPROVED = {"label": "fix-approved", "hand_back": "needs-design"}
COMMENT_URL = "https://github.com/current/project/issues/12#issuecomment-1"


class GitHub:
    """One issue's state, and every write SecondShift makes to it."""

    def __init__(self, labels: tuple[str, ...] = ("bug", "fix-approved")) -> None:
        self.state: IssueState | None = IssueState(True, labels, pull_request=False)
        self.comments: list[str] = []
        self.removed: list[str] = []
        self.added: list[str] = []
        self.refuse_labels = False
        self.permissions: list[dict[str, str]] = []

    def access(self, repository: Repository, permissions: dict[str, str]) -> Any:
        self.permissions.append(permissions)
        return ACCESS

    def read(self, token: str, full_name: str, number: int) -> IssueState | None:
        assert (token, full_name, number) == ("ghs_token", "current/project", 12)
        return self.state

    def comment(self, token: str, full_name: str, number: int, body: str) -> str:
        self.comments.append(body)
        return COMMENT_URL

    def remove(self, token: str, full_name: str, number: int, label: str) -> None:
        self.removed.append(label)

    def add(self, token: str, full_name: str, number: int, labels: list[str]) -> None:
        if self.refuse_labels:
            raise GitHubError("GitHub did not add the labels: Forbidden")
        self.added.extend(labels)


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> GitHub:
    fake = GitHub()
    monkeypatch.setattr(hand_back_delivery, "github_access", fake.access)
    monkeypatch.setattr(hand_back_delivery, "issue_state", fake.read)
    monkeypatch.setattr(hand_back_delivery, "comment_on_issue", fake.comment)
    monkeypatch.setattr(hand_back_delivery, "remove_issue_label", fake.remove)
    monkeypatch.setattr(hand_back_delivery, "add_issue_labels", fake.add)
    return fake


@pytest.fixture
def run(db: object) -> Run:
    """A finished run of a job that may hand issues back."""
    run = execution_run(db)
    Repository.objects.filter(pk=run.repository.pk).update(allow_issues=True)
    run.status = Run.Status.SUCCEEDED
    run.finished_at = timezone.now()
    run.execution_plan["approved_issues"] = APPROVED
    run.save()
    return Run.objects.get(pk=run.pk)


def handed_back(run: Run, number: int = 12) -> None:
    RunHandBack.objects.create(
        run=run, call_id="call-1", number=number, comment="**Needs a person.**"
    )


def delivery(run: Run) -> RunDelivery:
    return RunDelivery.objects.get(run=run, kind=RunDelivery.Kind.HAND_BACK)


def tool_context(run: Run, call_id: str, allowed: bool = True) -> RunContext:
    deps = WorkspaceToolDeps(
        run_id=run.pk,
        session_id="session",
        max_output_chars=1000,
        deadline=datetime(2100, 1, 1, tzinfo=UTC),
        hand_back=allowed,
    )
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_call_id=call_id)


def test_the_manifest_needs_an_approval_label() -> None:
    approved = ApprovedIssuesManifest.model_validate({"label": "fix-approved"})
    assert approved.hand_back is None
    with pytest.raises(ValidationError):
        ApprovedIssuesManifest.model_validate({"hand_back": "needs-design"})
    with pytest.raises(ValidationError):
        ApprovedIssuesManifest.model_validate({"label": "a", "hand_back": "A"})


def test_a_job_with_approved_issues_plans_its_labels(run: Run) -> None:
    job = run.job
    job.approved_issues = APPROVED
    job.save()

    plan = build_execution_plan(job)

    assert plan.approved_issues is not None
    assert (plan.approved_issues.label, plan.approved_issues.hand_back) == (
        "fix-approved",
        "needs-design",
    )
    job.approved_issues = None
    job.save()
    assert build_execution_plan(job).approved_issues is None


def test_the_prompt_explains_the_tool_only_when_the_job_has_it(run: Run) -> None:
    prompt = run_prompt(run)
    # The skill needn't name the label. The prompt does.
    assert "with the label fix-approved. Work only on open issues" in prompt
    assert "hand_back_issue" in prompt
    assert "removes fix-approved and adds needs-design" in prompt
    run.execution_plan["approved_issues"] = None
    run.save()
    assert "hand_back_issue" not in run_prompt(Run.objects.get(pk=run.pk))


def test_the_tool_is_hidden_without_hand_back(run: Run) -> None:
    definition = ToolDefinition(name=hand_back.TOOL_NAME)

    def shown(allowed: bool) -> ToolDefinition | None:
        return asyncio.run(
            hand_back.only_with_hand_back(
                tool_context(run, "call", allowed), definition
            )
        )

    assert shown(True) is definition
    assert shown(False) is None


@pytest.mark.django_db
def test_a_run_hands_back_one_issue(run: Run) -> None:
    first = hand_back.hand_back_issue(tool_context(run, "call-1"), 12, " Plan ")
    assert first["handed_back"] is True
    # A replayed call finds the same hand-back.
    assert hand_back.hand_back_issue(tool_context(run, "call-1"), 12, "Plan")[
        "handed_back"
    ]
    second = hand_back.hand_back_issue(tool_context(run, "call-2"), 13, "Other")

    assert second["handed_back"] is False
    assert "already handed back #12" in second["note"]
    saved = RunHandBack.objects.get(run=run)
    assert (saved.number, saved.comment) == (12, "Plan")


def test_a_hand_back_comments_and_swaps_the_labels(run: Run, github: GitHub) -> None:
    github_source(run, Connection.AuthStrategy.API_KEY, attribution=True)
    handed_back(run)

    workflows.hand_back_step(run.pk)

    [comment] = github.comments
    assert comment.startswith("**Needs a person.**\n\n---\n\n")
    assert f"run {run.pk}" in comment
    assert (github.removed, github.added) == (["fix-approved"], ["needs-design"])
    # Only issues, never contents or pull requests.
    assert github.permissions == [{"issues": "write"}]
    done = delivery(run)
    assert done.status == RunDelivery.Status.DELIVERED
    assert done.target == "Issue #12"
    assert done.message == (
        "Handed #12 back. Removed fix-approved and added needs-design."
    )
    assert done.external_url == COMMENT_URL


@pytest.mark.parametrize(
    "state",
    [
        None,
        IssueState(True, ("bug",), pull_request=False),
        IssueState(False, ("fix-approved",), pull_request=False),
        IssueState(True, ("fix-approved",), pull_request=True),
    ],
    ids=["missing", "not-approved", "closed", "pull-request"],
)
def test_only_an_open_approved_issue_is_touched(
    run: Run, github: GitHub, state: IssueState | None
) -> None:
    # The agent names the issue. The labels decide whether SecondShift acts.
    github.state = state
    handed_back(run)

    workflows.hand_back_step(run.pk)

    assert (github.comments, github.removed, github.added) == ([], [], [])
    skipped = delivery(run)
    assert skipped.status == RunDelivery.Status.SKIPPED
    assert skipped.message == (
        "#12 is not an open issue with fix-approved, so it was left alone."
    )


def test_a_retry_does_not_comment_twice(
    run: Run,
    github: GitHub,
    queue: Any,  # noqa: F811
) -> None:
    github.refuse_labels = True
    handed_back(run)

    workflows.hand_back_step(run.pk)
    failed = delivery(run)
    assert failed.status == RunDelivery.Status.FAILED
    assert failed.message == "GitHub did not add the labels: Forbidden"
    assert deliveries.retryable(failed)

    deliveries.retry_delivery(run, failed.pk)
    options, *args = queue.enqueue.call_args.args
    assert options["workflow_name"] == RETRY_HAND_BACK_WORKFLOW
    assert args == [run.pk]
    # The comment removed nothing yet the first time, so the label is still
    # there. A retry must still act once the comment is posted.
    github.state = IssueState(True, ("needs-design",), pull_request=False)
    github.refuse_labels = False
    workflows.hand_back_step(run.pk)

    assert len(github.comments) == 1
    assert delivery(run).status == RunDelivery.Status.DELIVERED
    assert delivery(run).attempts == 2


def test_a_run_that_hands_nothing_back_has_no_delivery(
    run: Run, github: GitHub
) -> None:
    workflows.hand_back_step(run.pk)

    assert not RunDelivery.objects.filter(
        run=run, kind=RunDelivery.Kind.HAND_BACK
    ).exists()


def test_without_issues_allowed_nothing_is_touched(run: Run, github: GitHub) -> None:
    Repository.objects.filter(pk=run.repository.pk).update(allow_issues=False)
    handed_back(run)

    workflows.hand_back_step(Run.objects.get(pk=run.pk).pk)

    assert github.comments == []
    assert delivery(run).message == (
        "An owner has not allowed issues for this repository."
    )
