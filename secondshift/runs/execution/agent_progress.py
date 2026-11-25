"""A subscription agent's turns, token usage and models while it works.

The agent wrapper sends them to the run's broker every few seconds (see
secondshift.agent.image.progress). They are counts the CLI reported, so they
are checked like the final result. They show on the run as it goes, and stay
when the agent's container stops before it reports.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from secondshift.agent.image.outcomes import MAX_MODELS, MODEL_NAME
from secondshift.runs.dao import save_agent_progress
from secondshift.runs.models import Run

UsageKey = Annotated[str, Field(max_length=64, pattern=r"^[a-z_]+$")]
ModelName = Annotated[str, Field(pattern=f"^{MODEL_NAME.pattern}$")]


class AgentProgress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    turns: int | None = Field(default=None, ge=0, le=1_000_000)
    usage: dict[UsageKey, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict, max_length=20
    )
    models: list[ModelName] = Field(default_factory=list, max_length=MAX_MODELS)


class ProgressRefused(ValueError):
    pass


def record_progress(run: Run, params: Any) -> None:
    """Keep the latest counts on the run. Raises ValidationError for bad ones."""
    backend = run.plan.execution.backend
    if backend.uses_model_api:
        # The gateway meters the built-in agent's model calls itself.
        raise ProgressRefused("This run's usage is metered by the gateway.")
    progress = AgentProgress.model_validate(params or {})
    save_agent_progress(
        run.pk,
        "Agent progress.",
        {
            "backend": backend,
            "usage": progress.usage,
            "models": progress.models,
            "turns": progress.turns,
        },
    )
