from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.models import Model, ModelResolutionContext

from secondshift.jobs.agent_policy import AgentPolicy
from secondshift.runs.execution.budgets import FinishWithinBudget
from secondshift.runs.execution.deadlines import check_deadline
from secondshift.runs.execution.model_gateway import (
    issue_model_gateway_access,
    pydantic_model,
)
from secondshift.runs.models import Run

RUN_MODEL_ID = "secondshift-run-model"


@dataclass(frozen=True)
class AgentLimits(AgentPolicy):
    def usage_limits(self) -> UsageLimits:
        """Translate server policy to Pydantic AI's native run limits."""
        return UsageLimits(
            request_limit=self.max_turns,
            tool_calls_limit=self.max_tool_calls,
        )


async def resolve_run_model(
    context: ModelResolutionContext[Any], model_id: str
) -> Model | None:
    """Build gateway access inside the durable model-request step.

    Only the run ID crosses the DBOS boundary. The short-lived gateway token is
    created and consumed inside the step and is never serialized into workflow
    inputs, outputs, or agent dependencies.
    """
    if model_id != RUN_MODEL_ID:
        return None
    check_deadline(context.deps.deadline)
    # Pydantic AI resolves models inside its event loop, where Django refuses
    # synchronous database access.
    return await sync_to_async(run_model)(int(context.deps.run_id))


def run_model(run_id: int) -> Model:
    run = Run.objects.select_related("job").get(pk=run_id)
    return pydantic_model(issue_model_gateway_access(run))


def build_agent(
    *,
    instructions: str,
    tools: Sequence[Any] = (),
    deps_type: type[Any] = object,
) -> Agent[Any, str]:
    """Build the durable orchestrator-side agent for one run.

    Tools supplied here are server-built wrappers around the runner or capability
    broker. Repository content cannot register tools or change durability.
    """
    return Agent(
        model=RUN_MODEL_ID,
        name="secondshift",
        instructions=instructions,
        deps_type=deps_type,
        tools=tools,
        defer_model_check=True,
        capabilities=[
            ResolveModelId(resolve_run_model),
            FinishWithinBudget(),
            DBOSDurability(name="secondshift"),
        ],
    )
