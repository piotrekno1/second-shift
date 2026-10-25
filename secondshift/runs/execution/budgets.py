"""Run budgets: model requests, tool calls and tokens.

The agent sees when it has to finish. On its last allowed model request, or
once its tool calls are used up, its tools are hidden and it is told to write
the report with what it has. A run that still runs out keeps what the model
wrote so far as a partial report. So does a run that times out.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from secondshift.config.constants import TOKEN_BUDGET_MESSAGE
from secondshift.jobs.limits import RunLimit, raise_hint

BUDGET_HEADLINE = "Budget used up"
FINISH_INSTRUCTIONS = (
    "This run has reached its budget, so no tools are available. Write the final "
    "report now from what you have found. Say what you could not check."
)
PARTIAL_REPORT_NOTE = (
    "_The run stopped at one of its limits before the agent finished. "
    "This is what it wrote so far._"
)


class Budget(StrEnum):
    REQUESTS = "requests"
    TOOL_CALLS = "tool_calls"
    TOKENS = "tokens"


@dataclass(frozen=True)
class BudgetLimit:
    label: str
    limit: RunLimit


BUDGET_LIMITS = {
    Budget.REQUESTS: BudgetLimit("model requests", RunLimit.MAX_TURNS),
    Budget.TOOL_CALLS: BudgetLimit("tool calls", RunLimit.MAX_TOOL_CALLS),
    Budget.TOKENS: BudgetLimit("tokens", RunLimit.TOKEN_BUDGET),
}


def spent_budget(context: RunContext[Any]) -> Budget | None:
    """The budget that leaves the agent no room for another tool round."""
    limits = context.usage_limits
    if limits is None:
        return None
    usage = context.usage
    # The request about to be made is not counted yet.
    if limits.request_limit is not None and usage.requests + 1 >= limits.request_limit:
        return Budget.REQUESTS
    if (
        limits.tool_calls_limit is not None
        and usage.tool_calls >= limits.tool_calls_limit
    ):
        return Budget.TOOL_CALLS
    return None


class FinishWithinBudget(AbstractCapability[Any]):
    """Hide the tools and ask for the report once the budget is spent."""

    async def prepare_tools(
        self, ctx: RunContext[Any], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        return [] if spent_budget(ctx) else tool_defs

    def get_instructions(self) -> Any:
        def instructions(ctx: RunContext[Any]) -> str:
            return FINISH_INSTRUCTIONS if spent_budget(ctx) else ""

        return instructions


def reached_budget(
    usage: RunUsage, max_turns: int, max_tool_calls: int
) -> Budget | None:
    """The budget a finished run used in full, if any."""
    if usage.requests >= max_turns:
        return Budget.REQUESTS
    if usage.tool_calls >= max_tool_calls:
        return Budget.TOOL_CALLS
    return None


def exhausted_budget(messages: Sequence[ModelMessage], max_turns: int) -> Budget:
    """Which native limit stopped the run, from the messages it produced."""
    responses = sum(isinstance(message, ModelResponse) for message in messages)
    return Budget.REQUESTS if responses >= max_turns else Budget.TOOL_CALLS


def is_token_budget_refusal(error: BaseException) -> bool:
    if not isinstance(error, ModelHTTPError) or error.status_code != 429:
        return False
    return TOKEN_BUDGET_MESSAGE in str(error.body)


def budget_reason(budget: Budget, limit: int) -> str:
    spec = BUDGET_LIMITS[budget]
    return (
        f"Stopped at {limit:,} {spec.label}, the run's limit. "
        f"{raise_hint(spec.limit, limit)}"
    )


def budget_reached_message(budget: Budget) -> str:
    return (
        f"The run used all its {BUDGET_LIMITS[budget].label}, so the agent "
        "wrote its report without further tool calls."
    )


def budget_summary(budget: Budget, limit: int) -> str:
    return f"{BUDGET_HEADLINE}: {budget_reason(budget, limit)}"


def partial_report(messages: Sequence[ModelMessage]) -> str:
    """What the model wrote and which tools it called before it was stopped."""
    texts: list[str] = []
    calls: Counter[str] = Counter()
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if isinstance(part, TextPart) and part.content.strip():
                texts.append(part.content.strip())
            elif isinstance(part, ToolCallPart):
                calls[part.tool_name] += 1
    if not texts and not calls:
        return ""
    sections = [PARTIAL_REPORT_NOTE, *texts]
    if calls:
        sections.append(
            "Tool calls made:\n"
            + "\n".join(f"- `{name}`: {count}" for name, count in calls.items())
        )
    return "\n\n".join(sections)
