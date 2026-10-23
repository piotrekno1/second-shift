import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.durable_exec.dbos import DBOSDurability

from secondshift.automations import agent
from secondshift.automations.agent import RUN_MODEL_ID, AgentLimits, agent_plan
from secondshift.automations.execution import WORKSPACE_AGENT
from secondshift.automations.tests.test_workflows import execution_run


def test_agent_limits_use_pydantic_ai_limits() -> None:
    limits = AgentLimits(max_turns=12, max_tool_calls=30)

    usage_limits = limits.usage_limits()

    assert usage_limits.request_limit == 12
    assert usage_limits.tool_calls_limit == 30


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


def test_agent_plan_is_redacted() -> None:
    limits = AgentLimits(max_turns=1, max_tool_calls=0)

    assert agent_plan(limits) == {
        "driver": "pydantic-ai",
        "protocol": "pydantic-ai-v1",
        "limits": {
            "max_turns": 1,
            "max_tool_calls": 0,
            "max_tool_result_chars": 65_536,
        },
    }


def test_workspace_agent_uses_deferred_model_resolution_and_dbos() -> None:
    assert WORKSPACE_AGENT.name == "secondshift"
    assert WORKSPACE_AGENT.model == RUN_MODEL_ID
    assert any(
        isinstance(capability, DBOSDurability)
        for capability in WORKSPACE_AGENT.root_capability.capabilities
    )
    assert any(
        isinstance(capability, ResolveModelId)
        for capability in WORKSPACE_AGENT.root_capability.capabilities
    )


@pytest.mark.django_db(transaction=True)
def test_run_model_resolves_from_inside_an_event_loop(monkeypatch) -> None:
    run = execution_run(None)
    issued: list[int] = []
    monkeypatch.setattr(
        agent, "issue_model_gateway_access", lambda r: issued.append(r.pk) or "access"
    )
    monkeypatch.setattr(agent, "pydantic_model", lambda access: f"model:{access}")
    context = SimpleNamespace(deps=SimpleNamespace(run_id=run.pk))

    # Pydantic AI awaits the resolver on its event loop, where Django forbids
    # synchronous queries; this used to raise SynchronousOnlyOperation.
    resolved = asyncio.run(agent.resolve_run_model(context, RUN_MODEL_ID))
    other = asyncio.run(agent.resolve_run_model(context, "some-other-model"))

    assert (resolved, other, issued) == ("model:access", None, [run.pk])
