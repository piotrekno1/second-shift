from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.files.storage import storages
from django.utils import timezone
from pydantic_ai import Tool, UsageLimits
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from secondshift.automations import workflows
from secondshift.automations.budgets import FINISH_INSTRUCTIONS, PARTIAL_REPORT_NOTE
from secondshift.automations.execution import (
    WORKSPACE_AGENT,
    WorkspaceToolDeps,
    budget_result,
    execute_agent_run,
)
from secondshift.automations.failures import describe_failure
from secondshift.automations.models import Run, RunEvent
from secondshift.automations.runner_protocol import CommandResult
from secondshift.automations.tests.test_execution import LATER
from secondshift.automations.tests.test_workflows import execution_run
from secondshift.automations.tool_policy import LOCAL_TOOLS
from secondshift.config.constants import TOKEN_BUDGET_MESSAGE

ModelFunction = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def read_readme() -> ToolCallPart:
    return ToolCallPart("workspace_read_file", {"path": "README.md"})


@pytest.fixture
def budget_run(
    db: object, settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Run]:
    settings.STORAGES = {
        **settings.STORAGES,
        "run_artifacts": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": tmp_path / "artifacts", "base_url": None},
        },
    }
    storages._storages.clear()
    monkeypatch.setattr(
        "secondshift.automations.execution.capture_run_input_step", lambda *args: None
    )
    monkeypatch.setattr(
        "secondshift.automations.execution.execute_runner_command_step",
        lambda session_id, call_id, *args: CommandResult(
            call_id=call_id, exit_code=0, output="# Project"
        ),
    )

    def make(
        max_turns: int = 10, max_tool_calls: int = 10, token_budget: int = 2_000_000
    ) -> Run:
        run = execution_run(db)
        run.started_at = timezone.now()
        run.input_snapshot = {"skill_path": ".agents/skills/review/SKILL.md"}
        run.execution_plan["agent"] = {
            "tools": list(LOCAL_TOOLS),
            "limits": {
                "max_turns": max_turns,
                "max_tool_calls": max_tool_calls,
                "max_tool_result_chars": 1_000,
                "token_budget": token_budget,
            },
        }
        run.save()
        return run

    return make


def run_with(model: ModelFunction, run: Run) -> dict[str, Any]:
    with WORKSPACE_AGENT.override(model=FunctionModel(model)):
        return execute_agent_run(run, f"run-{run.pk}")


def stored_report(result: dict[str, Any]) -> str:
    with storages["run_artifacts"].open(result["output"]["path"]) as stored:
        return stored.read().decode()


@pytest.mark.django_db
def test_the_last_request_has_no_tools_and_asks_for_the_report(
    budget_run: Callable[..., Run],
) -> None:
    seen: list[tuple[bool, str]] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append((bool(info.function_tools), info.instructions or ""))
        if info.function_tools:
            return ModelResponse(parts=[read_readme()])
        return ModelResponse(parts=[TextPart("Partial review: README is fine.")])

    result = run_with(model, budget_run(max_turns=3))

    assert [has_tools for has_tools, _ in seen] == [True, True, False]
    assert FINISH_INSTRUCTIONS not in seen[0][1]
    assert FINISH_INSTRUCTIONS in seen[-1][1]
    assert result["succeeded"] is True
    assert result["details"] == {"budget_reached": "requests"}
    assert stored_report(result) == "Partial review: README is fine."


@pytest.mark.django_db
def test_spent_tool_calls_also_end_with_a_report(
    budget_run: Callable[..., Run],
) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if info.function_tools:
            return ModelResponse(parts=[read_readme()])
        return ModelResponse(parts=[TextPart("Done with one file.")])

    result = run_with(model, budget_run(max_turns=10, max_tool_calls=1))

    assert result["succeeded"] is True
    assert result["details"] == {"budget_reached": "tool_calls"}


@pytest.mark.django_db
def test_a_run_within_budget_reports_nothing_extra(
    budget_run: Callable[..., Run],
) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("All good.")])

    result = run_with(model, budget_run())

    assert result["succeeded"] is True
    assert result["details"] == {}


@pytest.mark.django_db
def test_running_out_of_tool_calls_keeps_a_partial_report(
    budget_run: Callable[..., Run],
) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Three calls at once when only two are left.
        return ModelResponse(
            parts=[TextPart("The tests look thin."), *[read_readme()] * 3]
        )

    result = run_with(model, budget_run(max_tool_calls=2))

    assert result["succeeded"] is False
    assert result["summary"] == (
        "Budget used up: Stopped at 2 tool calls, the run's limit. "
        "Raise it in the job's limits."
    )
    assert result["details"] == {
        "budget_exhausted": "tool_calls",
        "partial_report": True,
    }
    report = stored_report(result)
    assert report.startswith(PARTIAL_REPORT_NOTE)
    assert "The tests look thin." in report
    assert "`workspace_read_file`: 3" in report


@pytest.mark.django_db
def test_a_model_that_ignores_the_last_request_runs_out(
    budget_run: Callable[..., Run],
) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Asks for a tool even when none is offered.
        return ModelResponse(parts=[read_readme()])

    result = run_with(model, budget_run(max_turns=2))

    assert result["succeeded"] is False
    assert result["summary"].startswith("Budget used up: Stopped at 2 model requests")
    assert result["summary"].endswith("Raise it in the job's limits.")
    assert result["details"]["budget_exhausted"] == "requests"


@pytest.mark.django_db
def test_the_gateway_token_budget_ends_the_run_with_its_work(
    budget_run: Callable[..., Run], settings: Any
) -> None:
    settings.MODEL_RUN_TOKEN_BUDGET_CEILING = 50_000

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) > 1:
            raise ModelHTTPError(
                429, "test", body={"error": {"message": TOKEN_BUDGET_MESSAGE}}
            )
        return ModelResponse(parts=[TextPart("Started on the README."), read_readme()])

    result = run_with(model, budget_run(token_budget=50_000))

    assert result["succeeded"] is False
    assert result["summary"].startswith("Budget used up: Stopped at 50,000 tokens")
    # At the server maximum only the server can allow more.
    assert "Set SECONDSHIFT_MODEL_RUN_TOKEN_BUDGET_CEILING" in result["summary"]
    assert "Started on the README." in stored_report(result)


@pytest.mark.django_db
def test_other_rate_limits_still_fail_the_run(
    budget_run: Callable[..., Run],
) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(429, "test", body={"error": {"message": "Slow down."}})

    with pytest.raises(ModelHTTPError):
        run_with(model, budget_run())


@pytest.mark.django_db
def test_an_exhausted_run_fails_with_its_partial_report_and_notifies(
    budget_run: Callable[..., Run],
    monkeypatch: pytest.MonkeyPatch,
    fake_stages: list[str],
) -> None:
    run = budget_run()
    notified: list[int] = []
    monkeypatch.setattr(workflows, "notify", notified.append)

    def exhausted(run: Run, session_id: str) -> dict[str, Any]:
        response = ModelResponse(parts=[TextPart("Half the checks ran.")])
        return budget_result(run, "requests", 24, [response])  # type: ignore[arg-type]

    monkeypatch.setattr(workflows, "execute_agent_run", exhausted)

    workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    view = describe_failure(run.summary)
    assert view.label == "Budget used up"
    assert view.reason.startswith("Stopped at 24 model requests")
    assert "Half the checks ran." in run.output_excerpt
    assert notified == [run.pk]
    event = RunEvent.objects.filter(run=run, level="error").get()
    assert event.metadata["budget_exhausted"] == "requests"


@pytest.mark.django_db
def test_a_run_that_used_its_budget_says_so_in_the_timeline(
    budget_run: Callable[..., Run],
    monkeypatch: pytest.MonkeyPatch,
    fake_stages: list[str],
) -> None:
    run = budget_run()
    monkeypatch.setattr(workflows, "notify", lambda run_id: None)
    monkeypatch.setattr(
        workflows,
        "execute_agent_run",
        lambda run, session_id: {
            "exit_code": 0,
            "summary": "Report.",
            "succeeded": True,
            "details": {"budget_reached": "requests"},
        },
    )

    workflows.execute_run(run.pk)

    run.refresh_from_db()
    assert run.status == Run.Status.SUCCEEDED
    warning = RunEvent.objects.get(run=run, level="warning")
    assert "used all its model requests" in warning.message


def test_the_last_request_hides_external_tools_too() -> None:
    seen: list[list[str]] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(sorted(tool.name for tool in info.function_tools))
        if info.function_tools:
            return ModelResponse(parts=[ToolCallPart("github_search", {})])
        return ModelResponse(parts=[TextPart("Done.")])

    def github_search() -> str:
        return "no results"

    deps = WorkspaceToolDeps(
        run_id=1, session_id="run-1", max_output_chars=100, deadline=LATER
    )
    with WORKSPACE_AGENT.override(model=FunctionModel(model)):
        WORKSPACE_AGENT.run_sync(
            "Review.",
            deps=deps,
            usage_limits=UsageLimits(request_limit=2),
            toolsets=[FunctionToolset([Tool(github_search)])],
        )

    assert "github_search" in seen[0]
    assert seen[1] == []


@pytest.mark.django_db
def test_a_timed_out_run_keeps_a_partial_report(
    budget_run: Callable[..., Run], settings: Any
) -> None:
    settings.RUN_TIMEOUT_SECONDS = 900
    run = budget_run()
    run.started_at = timezone.now() - timedelta(hours=1)
    run.save(update_fields=["started_at"])

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Checked the CI config."), read_readme()])

    result = run_with(model, run)

    assert result["succeeded"] is False
    assert describe_failure(result["summary"]).label == "Timed out"
    assert result["details"] == {"timed_out": True, "partial_report": True}
    assert "Checked the CI config." in stored_report(result)
