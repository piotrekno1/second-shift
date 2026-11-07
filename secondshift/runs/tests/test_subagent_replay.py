"""A worker restart during a run with a sub-agent replays without repeating work.

This runs a real DBOS workflow. Forking it from a step replays every earlier
step from its record, as recovery after a restart does, and runs the rest again.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from dbos import DBOS, SetWorkflowID
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from secondshift.runner.protocol import CommandRequest, CommandResult
from secondshift.runs.execution.run import execute_agent_run
from secondshift.runs.execution.subagents import TOOL_NAME
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.tests.test_budgets import budget_run  # noqa: F401
from secondshift.runs.tests.test_subagents import (
    delegating_run,
    tool_names,
    tool_returns,
)


@DBOS.workflow(name="secondshift.test_subagent_replay")
def agent_run_workflow(run_id: int) -> str:
    run = Run.objects.select_related("job").get(pk=run_id)
    return str(execute_agent_run(run, f"run-{run_id}")["summary"])


class CountingRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def execute(self, session_id: str, request: CommandRequest) -> CommandResult:
        self.commands.append(request.argv)
        return CommandResult(call_id=request.call_id, exit_code=0, output="# Project")


@pytest.fixture
def dbos(tmp_path: Path) -> Iterator[None]:
    DBOS(
        config={
            "name": "secondshift-test",
            "system_database_url": f"sqlite:///{tmp_path / 'dbos.sqlite'}",
        }
    )
    DBOS.launch()
    yield
    DBOS.destroy()


@pytest.mark.django_db(transaction=True)
def test_a_forked_run_replays_its_subagent_from_records(
    budget_run: Callable[..., Run],  # noqa: F811
    dbos: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = delegating_run(budget_run)
    runner = CountingRunner()
    # Real command steps, so a replay that repeated them would show here.
    monkeypatch.undo()
    monkeypatch.setattr(
        "secondshift.runs.execution.run.capture_run_input_step", lambda *args: None
    )
    monkeypatch.setattr(
        "secondshift.runs.execution.workspace_tools.get_runner_client", lambda: runner
    )
    calls: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returns = tool_returns(messages)
        if TOOL_NAME in tool_names(info):
            calls.append("run")
            if not returns:
                return ModelResponse(
                    [ToolCallPart(TOOL_NAME, {"task": "Check README.md."})]
                )
            return ModelResponse([TextPart(f"Report: {returns[-1]}")])
        calls.append("sub-agent")
        if not returns:
            return ModelResponse(
                [ToolCallPart("workspace_read_file", {"path": "README.md"})]
            )
        return ModelResponse([TextPart("README.md has a title.")])

    # Both agents resolve their model inside the durable model step.
    monkeypatch.setattr(
        "secondshift.runs.execution.agent.run_model",
        lambda run_id: FunctionModel(model),
    )

    workflow_id = f"subagent-replay-{run.pk}"
    with SetWorkflowID(workflow_id):
        first = agent_run_workflow(run.pk)

    assert first == "Report: README.md has a title."
    assert calls == ["run", "sub-agent", "sub-agent", "run"]
    assert runner.commands == [("cat", "--", "README.md")]
    steps = DBOS.list_workflow_steps(workflow_id)
    model_steps = [step for step in steps if "request" in step["function_name"]]
    assert len(model_steps) == 4
    events = list(RunEvent.objects.filter(run=run).values_list("message", flat=True))

    # Replay everything up to the run's last model request.
    calls.clear()
    forked = DBOS.fork_workflow(workflow_id, model_steps[-1]["function_id"])

    assert forked.get_result() == first
    assert calls == ["run"]
    assert runner.commands == [("cat", "--", "README.md")]
    assert (
        list(RunEvent.objects.filter(run=run).values_list("message", flat=True))
        == events
    )
