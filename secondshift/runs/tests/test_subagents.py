from collections.abc import Callable
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from secondshift.config.constants import AgentBackend
from secondshift.runs.execution.run import (
    WORKSPACE_AGENT,
    execute_agent_run,
    run_prompt,
)
from secondshift.runs.execution.subagents import SUBAGENT, TOOL_NAME, begin
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.tests.test_budgets import budget_run  # noqa: F401

ModelFunction = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def tool_names(info: AgentInfo) -> list[str]:
    return [tool.name for tool in info.function_tools]


def tool_returns(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


def delegating_run(make: Callable[..., Run], subagents: int = 2, **limits: int) -> Run:
    run = make(**limits)
    run.execution_plan["agent"]["subagents"] = subagents
    run.save(update_fields=["execution_plan"])
    return run


def execute(run: Run, parent: ModelFunction, child: ModelFunction) -> dict[str, Any]:
    with (
        WORKSPACE_AGENT.override(model=FunctionModel(parent)),
        SUBAGENT.override(model=FunctionModel(child)),
    ):
        return execute_agent_run(run, f"run-{run.pk}")


def subagent_events(run: Run) -> list[dict[str, Any]]:
    return [
        event.metadata
        for event in RunEvent.objects.filter(run=run, metadata__has_key="subagent")
    ]


@pytest.mark.django_db(transaction=True)
def test_a_subagent_starts_fresh_and_reports_back(budget_run) -> None:  # noqa: F811
    run = delegating_run(budget_run)
    seen: dict[str, Any] = {}

    def parent(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returns = tool_returns(messages)
        if not returns:
            seen["parent_tools"] = tool_names(info)
            return ModelResponse(
                [ToolCallPart(TOOL_NAME, {"task": "Check README.md for a title."})]
            )
        seen["parent_got"] = returns[-1]
        return ModelResponse([TextPart("Report: the README has a title.")])

    def child(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.setdefault("child_tools", tool_names(info))
        seen.setdefault("child_prompts", prompts(messages))
        if not tool_returns(messages):
            return ModelResponse(
                [ToolCallPart("workspace_read_file", {"path": "README.md"})]
            )
        return ModelResponse([TextPart("Verified: README.md starts with # Project.")])

    result = execute(run, parent, child)

    assert result["succeeded"] is True
    assert TOOL_NAME in seen["parent_tools"]
    # One level only, and none of the run's own prompt.
    assert TOOL_NAME not in seen["child_tools"]
    assert "workspace_read_file" in seen["child_tools"]
    assert seen["child_prompts"] == ["Check README.md for a title."]
    assert seen["parent_got"] == "Verified: README.md starts with # Project."
    events = subagent_events(run)
    assert [(item["subagent"], item["number"]) for item in events] == [
        ("started", 1),
        ("finished", 1),
    ]
    assert events[1]["requests"] == 2
    assert events[1]["tool_calls"] == 1


@pytest.mark.django_db(transaction=True)
def test_subagents_share_the_runs_tool_call_budget(budget_run) -> None:  # noqa: F811
    # The run's call to start the sub-agent and the sub-agent's read use both.
    run = delegating_run(budget_run, max_tool_calls=2)
    child_tools: list[list[str]] = []
    parent_tools: list[list[str]] = []

    def parent(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        parent_tools.append(tool_names(info))
        if not tool_returns(messages):
            return ModelResponse([ToolCallPart(TOOL_NAME, {"task": "Read README.md"})])
        return ModelResponse([TextPart("Done.")])

    def child(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        child_tools.append(tool_names(info))
        if not tool_returns(messages):
            return ModelResponse(
                [ToolCallPart("workspace_read_file", {"path": "README.md"})]
            )
        return ModelResponse([TextPart("Read it.")])

    result = execute(run, parent, child)

    assert result["succeeded"] is True
    # After the sub-agent's read, the budget is spent for both of them.
    assert child_tools[0] and child_tools[1] == []
    assert parent_tools[1] == []
    assert result["details"]["budget_reached"] == "tool_calls"


@pytest.mark.django_db(transaction=True)
def test_a_run_starts_no_more_subagents_than_its_plan_allows(budget_run) -> None:  # noqa: F811
    run = delegating_run(budget_run, subagents=1)
    answers: list[str] = []

    def parent(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returns = tool_returns(messages)
        if len(returns) < 2:
            return ModelResponse(
                [ToolCallPart(TOOL_NAME, {"task": f"Task {len(returns) + 1}"})]
            )
        answers.extend(returns)
        return ModelResponse([TextPart("Done.")])

    def child(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse([TextPart("Checked.")])

    execute(run, parent, child)

    assert answers[0] == "Checked."
    assert "the most sub-agents it may, 1" in answers[1]
    assert [item["subagent"] for item in subagent_events(run)] == [
        "started",
        "finished",
    ]


@pytest.mark.django_db(transaction=True)
def test_the_run_keeps_a_request_for_its_own_report(budget_run) -> None:  # noqa: F811
    # One request to start the sub-agent and one for the report leave none.
    run = delegating_run(budget_run, max_turns=3)
    answers: list[str] = []

    def parent(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returns = tool_returns(messages)
        if not returns:
            return ModelResponse([ToolCallPart(TOOL_NAME, {"task": "Check it"})])
        answers.extend(returns)
        return ModelResponse([TextPart("Done.")])

    def child(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError("No sub-agent should start.")

    result = execute(run, parent, child)

    assert result["succeeded"] is True
    assert "too little of its budget" in answers[0]
    assert subagent_events(run) == []


@pytest.mark.django_db(transaction=True)
def test_without_subagents_the_tool_and_note_are_hidden(budget_run) -> None:  # noqa: F811
    run = delegating_run(budget_run, subagents=0)
    seen: list[list[str]] = []

    def parent(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(tool_names(info))
        return ModelResponse([TextPart("Done.")])

    execute(run, parent, parent)

    assert TOOL_NAME not in seen[0]
    assert TOOL_NAME not in run_prompt(run)
    run.execution_plan["agent"]["subagents"] = 2
    assert "up to 2 sub-agents" in run_prompt(run)


@pytest.mark.django_db(transaction=True)
def test_a_subscription_run_starts_no_subagents(budget_run) -> None:  # noqa: F811
    run = delegating_run(budget_run, subagents=2)
    run.execution_plan["execution"] = {"backend": AgentBackend.CLAUDE_CODE}

    assert TOOL_NAME not in run_prompt(run)


@pytest.mark.django_db(transaction=True)
def test_a_replayed_start_is_counted_once(budget_run) -> None:  # noqa: F811
    run = delegating_run(budget_run, subagents=1)

    assert begin(run.pk, "call-1", 1, "Check it") == 1
    assert begin(run.pk, "call-1", 1, "Check it") == 1
    assert begin(run.pk, "call-2", 1, "Another") is None
    assert len(subagent_events(run)) == 1
