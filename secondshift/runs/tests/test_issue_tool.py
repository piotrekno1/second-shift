from datetime import UTC, datetime

import anyio
import pytest
from pydantic_ai import ModelRetry, RunContext, RunUsage
from pydantic_ai.models.test import TestModel

from secondshift.config.constants import MAX_RUN_ISSUES, RunStart
from secondshift.jobs.models import Job
from secondshift.runs.execution.agent_broker import broker_tools
from secondshift.runs.execution.issues import TOOL_NAME, issue_tool, propose_issue
from secondshift.runs.execution.run import run_prompt
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps
from secondshift.runs.models import Run, RunIssue
from secondshift.runs.services import start_run
from secondshift.runs.tests.test_payload import job  # noqa: F401

LATER = datetime(2100, 1, 1, tzinfo=UTC)


def context(
    run: Run, call_id: str, limit: int = MAX_RUN_ISSUES
) -> RunContext[WorkspaceToolDeps]:
    deps = WorkspaceToolDeps(
        run_id=run.pk,
        session_id="run-1",
        max_output_chars=100_000,
        deadline=LATER,
        issue_limit=limit,
    )
    return RunContext(
        deps=deps, model=TestModel(), usage=RunUsage(), tool_call_id=call_id
    )


@pytest.fixture
def filing_run(job: Job) -> Run:  # noqa: F811
    Job.objects.filter(pk=job.pk).update(
        open_issues={"max": MAX_RUN_ISSUES, "labels": []}
    )
    return start_run(Job.objects.get(pk=job.pk), RunStart.MANUAL)


def test_runs_without_issues_do_not_offer_the_tool(job: Job) -> None:  # noqa: F811
    run = start_run(job, RunStart.MANUAL)

    assert run.execution_plan["issues"] is None
    assert TOOL_NAME not in run_prompt(run)
    assert anyio.run(issue_tool().prepare_tool_def, context(run, "1", 0)) is None
    assert TOOL_NAME not in broker_tools(run)


def test_a_filing_run_offers_the_tool(filing_run: Run) -> None:
    assert f"Call {TOOL_NAME}" in run_prompt(filing_run)
    assert anyio.run(issue_tool().prepare_tool_def, context(filing_run, "1"))
    assert TOOL_NAME in broker_tools(filing_run)


def test_proposals_are_kept_once_per_call(filing_run: Run) -> None:
    first = propose_issue(context(filing_run, "a"), "  Flaky   login test ", " Body ")
    # A replayed call finds its proposal.
    again = propose_issue(context(filing_run, "a"), "Flaky login test", "Body")

    assert first["proposed"] and again["proposed"]
    issue = RunIssue.objects.get(run=filing_run)
    assert (issue.title, issue.body) == ("Flaky login test", "Body")


def test_a_repeated_title_is_not_proposed_twice(filing_run: Run) -> None:
    propose_issue(context(filing_run, "a"), "Flaky login test", "Body")
    repeated = propose_issue(context(filing_run, "b"), "flaky LOGIN test", "Other")

    assert repeated["proposed"] is False
    assert RunIssue.objects.filter(run=filing_run).count() == 1


def test_proposals_stop_at_the_limit(filing_run: Run) -> None:
    for index in range(2):
        propose_issue(context(filing_run, str(index), 2), f"Issue {index}", "")
    refused = propose_issue(context(filing_run, "late", 2), "Issue 3", "")

    assert refused["proposed"] is False
    assert "Put the rest in the report" in refused["note"]
    assert RunIssue.objects.filter(run=filing_run).count() == 2


def test_a_blank_title_goes_back_to_the_model(filing_run: Run) -> None:
    with pytest.raises(ModelRetry, match="title"):
        propose_issue(context(filing_run, "a"), "   ", "Body")
