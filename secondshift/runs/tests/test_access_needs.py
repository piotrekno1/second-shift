from datetime import UTC, datetime

import pytest
from pydantic_ai import ModelRetry, RunContext, RunUsage
from pydantic_ai.models.test import TestModel

from secondshift.config.constants import RunStart
from secondshift.jobs.models import Job
from secondshift.runs.execution.access_needs import (
    MAX_SERVICES,
    TOOL_NAME,
    report_missing_access,
)
from secondshift.runs.execution.agent_broker import broker_tools
from secondshift.runs.execution.run import run_prompt
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps
from secondshift.runs.models import Run, RunAccessNeed
from secondshift.runs.serialization import serialize_run_detail
from secondshift.runs.services import start_run
from secondshift.runs.tests.test_payload import job  # noqa: F401

LATER = datetime(2100, 1, 1, tzinfo=UTC)


def context(run: Run, call_id: str) -> RunContext[WorkspaceToolDeps]:
    deps = WorkspaceToolDeps(
        run_id=run.pk, session_id="run-1", max_output_chars=100_000, deadline=LATER
    )
    return RunContext(
        deps=deps, model=TestModel(), usage=RunUsage(), tool_call_id=call_id
    )


@pytest.fixture
def run(job: Job) -> Run:  # noqa: F811
    return start_run(job, RunStart.MANUAL)


def test_every_run_offers_the_tool(run: Run) -> None:
    assert f"call {TOOL_NAME}" in run_prompt(run)
    assert TOOL_NAME in broker_tools(run)


def test_a_report_is_kept_and_shown_but_grants_nothing(run: Run) -> None:
    plan = run.execution_plan

    result = report_missing_access(
        context(run, "a"), " Linear ", ["create_issue", " create_issue"], "File it."
    )

    assert result["recorded"] is True
    need = RunAccessNeed.objects.get(run=run)
    assert (need.service, need.tools, need.reason) == (
        "Linear",
        ["create_issue"],
        "File it.",
    )
    run.refresh_from_db()
    assert run.execution_plan == plan
    detail = serialize_run_detail(run)
    assert [item.service for item in detail.access_needs] == ["Linear"]


def test_a_replayed_call_and_the_same_service_are_merged(run: Run) -> None:
    report_missing_access(context(run, "a"), "linear", ["create_issue"])
    report_missing_access(context(run, "a"), "linear", ["create_issue"])
    report_missing_access(context(run, "b"), "LINEAR", ["list_issues"])

    need = RunAccessNeed.objects.get(run=run)
    assert need.tools == ["create_issue", "list_issues"]


def test_reports_are_bounded(run: Run) -> None:
    for index in range(MAX_SERVICES):
        report_missing_access(context(run, str(index)), f"service-{index}")

    result = report_missing_access(context(run, "last"), "one-more")

    assert result["recorded"] is False
    assert RunAccessNeed.objects.filter(run=run).count() == MAX_SERVICES


def test_a_blank_service_goes_back_to_the_model(run: Run) -> None:
    with pytest.raises(ModelRetry):
        report_missing_access(context(run, "a"), "   ")
