import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from secondshift.runs.execution import agent
from secondshift.runs.execution.agent import RUN_MODEL_ID, AgentLimits
from secondshift.runs.tests.test_workflows import execution_run

LATER = datetime(2100, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "values",
    [
        {"max_turns": 0, "max_tool_calls": 1},
        {"max_turns": 1, "max_tool_calls": -1},
        {"max_turns": 1, "max_tool_calls": 1, "max_tool_result_chars": 0},
    ],
)
def test_agent_limits_reject_invalid_server_policy(values: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        AgentLimits(**values)


@pytest.mark.django_db(transaction=True)
def test_run_model_resolves_from_inside_an_event_loop(monkeypatch) -> None:
    run = execution_run(None)
    issued: list[int] = []
    monkeypatch.setattr(
        agent, "issue_model_gateway_access", lambda r: issued.append(r.pk) or "access"
    )
    monkeypatch.setattr(agent, "pydantic_model", lambda access: f"model:{access}")
    context = SimpleNamespace(deps=SimpleNamespace(run_id=run.pk, deadline=LATER))

    # Pydantic AI awaits the resolver on its event loop, where Django forbids
    # synchronous queries. This used to raise SynchronousOnlyOperation.
    resolved = asyncio.run(agent.resolve_run_model(context, RUN_MODEL_ID))
    other = asyncio.run(agent.resolve_run_model(context, "some-other-model"))

    assert (resolved, other, issued) == ("model:access", None, [run.pk])
