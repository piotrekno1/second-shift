"""Opening the issues a finished run's agent proposed."""

from typing import Any

import pytest
from django.utils import timezone

from secondshift.config.constants import MAX_RUN_ISSUES, RETRY_ISSUES_WORKFLOW, RunStart
from secondshift.github.errors import GitHubError
from secondshift.github.issues import Issue
from secondshift.jobs.models import Job
from secondshift.repositories.github_access import GitHubAccess
from secondshift.repositories.models import Repository
from secondshift.runs import deliveries, issue_delivery
from secondshift.runs.models import Run, RunDelivery, RunIssue
from secondshift.runs.orchestration import workflows
from secondshift.runs.reporting.notifications import run_report_payload
from secondshift.runs.services import create_run
from secondshift.runs.tests.test_publishing import queue  # noqa: F401
from secondshift.runs.tests.test_workflows import execution_run
from secondshift.workspaces.dao import workspace_owner

ACCESS = GitHubAccess(42, "current/project", "ghs_token", {})


class GitHub:
    """Open issues and earlier ones, by title. Fails a title on request."""

    def __init__(self, already_open: dict[str, int] | None = None) -> None:
        self.already_open = already_open or {}
        self.opened: list[tuple[str, str]] = []
        self.refuse: set[str] = set()
        self.permissions: list[dict[str, str]] = []

    def access(self, repository: Repository, permissions: dict[str, str]) -> Any:
        self.permissions.append(permissions)
        return ACCESS

    def find(self, token: str, full_name: str, title: str) -> Issue | None:
        number = self.already_open.get(title)
        return Issue(number, url(number)) if number else None

    def open(
        self, token: str, full_name: str, title: str, body: str, labels: list[str]
    ) -> Issue:
        assert (token, full_name) == ("ghs_token", "current/project")
        # Only the repository's labels. The agent cannot add any.
        assert labels == ["triage"]
        if title in self.refuse:
            raise GitHubError("GitHub did not open the issue: Validation Failed")
        self.opened.append((title, body))
        number = 100 + len(self.opened)
        return Issue(number, url(number))


def url(number: int) -> str:
    return f"https://github.com/current/project/issues/{number}"


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> GitHub:
    fake = GitHub({"Old bug": 7})
    monkeypatch.setattr(issue_delivery, "github_access", fake.access)
    monkeypatch.setattr(issue_delivery, "open_issue_titled", fake.find)
    monkeypatch.setattr(issue_delivery, "open_issue", fake.open)
    return fake


@pytest.fixture
def run(db: object) -> Run:
    """A finished run of a job that opens issues, with two proposals."""
    run = execution_run(db)
    Repository.objects.filter(pk=run.repository.pk).update(allow_issues=True)
    run.status = Run.Status.SUCCEEDED
    run.finished_at = timezone.now()
    run.execution_plan["issues"] = {"max": MAX_RUN_ISSUES, "labels": ["triage"]}
    run.save()
    RunDelivery.objects.create(
        run=run, kind=RunDelivery.Kind.ISSUES, target="Issues in current/project"
    )
    RunIssue.objects.create(
        run=run, call_id="1", title="Flaky login test", body="Steps"
    )
    RunIssue.objects.create(run=run, call_id="2", title="Old bug", body="")
    return Run.objects.get(pk=run.pk)


def delivery(run: Run) -> RunDelivery:
    return RunDelivery.objects.get(run=run, kind=RunDelivery.Kind.ISSUES)


def test_proposals_open_unless_already_open(run: Run, github: GitHub) -> None:
    workflows.open_issues_step(run.pk)

    [(title, body)] = github.opened
    assert title == "Flaky login test"
    assert body.startswith("Steps\n\n---\n\n")
    assert f"run #{run.pk}" in body
    # Only issues, never contents or pull requests.
    assert github.permissions == [{"issues": "write"}]
    done = delivery(run)
    assert done.status == RunDelivery.Status.DELIVERED
    assert done.message == "Opened 1 issue. 1 issue was already open."
    old = RunIssue.objects.get(run=run, title="Old bug")
    assert (old.number, old.already_open) == (7, True)
    payload = run_report_payload(run)
    assert payload["issues"] == [
        {
            "number": 101,
            "title": "Flaky login test",
            "url": url(101),
            "already_open": False,
        },
        {"number": 7, "title": "Old bug", "url": url(7), "already_open": True},
    ]
    assert f"• <{url(101)}|#101 Flaky login test>" in payload["text"]
    assert f"• <{url(7)}|#7 Old bug> (already open)" in payload["text"]


def test_titles_cannot_break_the_chat_link(run: Run, github: GitHub) -> None:
    RunIssue.objects.filter(run=run, call_id="1").update(title="Fix <a|b> & c")

    workflows.open_issues_step(run.pk)

    assert "#101 Fix &lt;a|b&gt; &amp; c>" in run_report_payload(run)["text"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"status": Run.Status.FAILED}, "The run did not succeed."),
        ("no proposals", "The agent proposed no issues."),
        ("not allowed", "An owner has not allowed issues for this repository."),
        ("local", "Local repositories cannot receive issues."),
    ],
)
def test_runs_that_cannot_open_issues_say_why(
    run: Run, github: GitHub, change: Any, reason: str
) -> None:
    if change == "no proposals":
        run.issues.all().delete()
    elif change == "not allowed":
        Repository.objects.filter(pk=run.repository.pk).update(allow_issues=False)
    elif change == "local":
        Repository.objects.filter(pk=run.repository.pk).update(
            source_type=Repository.SourceType.LOCAL
        )
    else:
        Run.objects.filter(pk=run.pk).update(**change)

    workflows.open_issues_step(run.pk)

    assert (delivery(run).status, delivery(run).message) == (
        RunDelivery.Status.SKIPPED,
        reason,
    )
    assert github.opened == []
    assert f"No issues opened. {reason}" in run_report_payload(run)["text"]


def test_a_retry_opens_only_the_rest(
    run: Run,
    github: GitHub,
    queue: Any,  # noqa: F811
) -> None:
    RunIssue.objects.create(run=run, call_id="3", title="Slow build", body="")
    github.refuse = {"Slow build"}

    workflows.open_issues_step(run.pk)
    failed = delivery(run)
    assert failed.status == RunDelivery.Status.FAILED
    assert failed.message == (
        "Opened 1 issue. 1 issue was already open. "
        "GitHub did not open the issue: Validation Failed"
    )
    assert "Some issues could not open." in run_report_payload(run)["text"]
    assert deliveries.retryable(failed)

    deliveries.retry_delivery(run, failed.pk)
    options, *args = queue.enqueue.call_args.args
    assert options["workflow_name"] == RETRY_ISSUES_WORKFLOW
    assert args == [run.pk]
    github.refuse = set()
    workflows.open_issues_step(run.pk)

    assert [title for title, _ in github.opened] == ["Flaky login test", "Slow build"]
    assert delivery(run).status == RunDelivery.Status.DELIVERED
    assert delivery(run).attempts == 2


def test_an_app_without_issues_permission_says_so(
    run: Run, github: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(repository: Repository, permissions: dict[str, str]) -> Any:
        raise GitHubError("HTTP 422 with ghs_details")

    monkeypatch.setattr(issue_delivery, "github_access", refused)

    workflows.open_issues_step(run.pk)

    assert delivery(run).message == issue_delivery.APP_WRITE_HINT


def test_runs_open_issues_before_they_notify(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        workflows, "open_issues_step", lambda run_id: order.append("issues")
    )
    monkeypatch.setattr(workflows, "notify", lambda run_id: order.append("notify"))

    workflows.deliver(run.pk)
    run.execution_plan["issues"] = None
    run.save()
    workflows.deliver(run.pk)

    assert order == ["issues", "notify", "notify"]


@pytest.mark.django_db
def test_each_run_of_a_filing_job_waits_to_open_issues(db: object) -> None:
    job = execution_run(db).job
    Job.objects.filter(pk=job.pk).update(
        open_issues={"max": MAX_RUN_ISSUES, "labels": []}
    )
    job.refresh_from_db()

    run = create_run(job, timezone.now(), started_by=RunStart.MANUAL)

    assert delivery(run).status == RunDelivery.Status.PENDING
    assert delivery(run).target == "Issues in current/project"


@pytest.mark.django_db
def test_the_job_and_run_pages_show_issues(
    client: Any, run: Run, github: GitHub
) -> None:
    Job.objects.filter(pk=run.job_id).update(
        open_issues={"max": MAX_RUN_ISSUES, "labels": []}
    )
    client.force_login(workspace_owner(run.job.workspace))
    workflows.open_issues_step(run.pk)

    job = client.get(f"/api/jobs/{run.job_id}").json()
    detail = client.get(f"/api/runs/{run.pk}").json()

    assert job["open_issues"] == {"max": MAX_RUN_ISSUES, "labels": []}
    assert job["issues_allowed"] is True
    assert [issue["number"] for issue in detail["issues"]] == [101, 7]
    assert detail["issues"][1]["already_open"] is True
