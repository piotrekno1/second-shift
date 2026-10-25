"""Run limits: how far one run may go.

Each limit has a server default and a server maximum, both set through
environment variables. A workspace owner can choose a value for the workspace
and for each job, up to the maximum. A job's value wins over the workspace's,
and the workspace's wins over the default. Repository configuration cannot set
limits.

The values that apply are frozen into the run's execution plan when the run is
created. Changing a limit never affects a run that is already queued or running.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from django.conf import settings

from secondshift.core.models import RunLimitOverrides
from secondshift.jobs.models import Job
from secondshift.workspaces.models import Workspace


class RunLimit(StrEnum):
    MAX_TURNS = "max_turns"
    MAX_TOOL_CALLS = "max_tool_calls"
    TOKEN_BUDGET = "token_budget"
    TIMEOUT_SECONDS = "timeout_seconds"


class LimitUnit(StrEnum):
    COUNT = "count"
    TOKENS = "tokens"
    SECONDS = "seconds"


@dataclass(frozen=True)
class LimitSpec:
    label: str
    help: str
    unit: LimitUnit
    # The Django setting for the default. Its maximum adds "_CEILING".
    setting: str
    minimum: int

    @property
    def env(self) -> str:
        return f"SECONDSHIFT_{self.setting}"

    @property
    def ceiling_env(self) -> str:
        return f"{self.env}_CEILING"

    def default(self) -> int:
        return int(getattr(settings, self.setting))

    def ceiling(self) -> int:
        return int(getattr(settings, f"{self.setting}_CEILING"))


LIMIT_SPECS = {
    RunLimit.MAX_TURNS: LimitSpec(
        "Model calls",
        "Shown on runs as turns, e.g. 5/24. The last one writes the report.",
        LimitUnit.COUNT,
        "AGENT_MAX_TURNS",
        minimum=1,
    ),
    RunLimit.MAX_TOOL_CALLS: LimitSpec(
        "Tool calls",
        "Reading files, searching and running commands.",
        LimitUnit.COUNT,
        "AGENT_MAX_TOOL_CALLS",
        minimum=0,
    ),
    RunLimit.TOKEN_BUDGET: LimitSpec(
        "Tokens",
        "Input and output tokens. The model gateway refuses calls past this.",
        LimitUnit.TOKENS,
        "MODEL_RUN_TOKEN_BUDGET",
        minimum=1_000,
    ),
    RunLimit.TIMEOUT_SECONDS: LimitSpec(
        "Time",
        "Counted from when the run starts. Time in the queue is free.",
        LimitUnit.SECONDS,
        "RUN_TIMEOUT_SECONDS",
        minimum=60,
    ),
}


class LimitError(ValueError):
    def __init__(self, message: str, ceiling_env: str = "") -> None:
        super().__init__(message)
        # The variable that raises the maximum, for people who run the server.
        self.ceiling_env = ceiling_env


@dataclass(frozen=True)
class LimitView:
    """One limit as an owner sees it at one level (workspace or job)."""

    key: str
    label: str
    help: str
    unit: str
    # What a run would get now.
    value: int
    # What this level chose, or None to use `inherited`.
    override: int | None
    # What applies when this level chooses nothing.
    inherited: int
    minimum: int
    maximum: int


def server_default(limit: RunLimit) -> int:
    # Settings refuse a maximum below its default, so no cap is needed.
    return LIMIT_SPECS[limit].default()


def capped(limit: RunLimit, value: int) -> int:
    # A maximum lowered after an owner chose a value still applies.
    return min(value, LIMIT_SPECS[limit].ceiling())


def workspace_value(workspace: Workspace, limit: RunLimit) -> int:
    chosen = getattr(workspace, limit)
    return capped(limit, chosen) if chosen is not None else server_default(limit)


def job_value(job: Job, limit: RunLimit) -> int:
    chosen = getattr(job, limit)
    if chosen is not None:
        return capped(limit, chosen)
    return workspace_value(job.workspace, limit)


def effective_limits(job: Job) -> dict[str, int]:
    """The limits a run of this job gets, for its execution plan."""
    return {str(limit): job_value(job, limit) for limit in RunLimit}


def limit_views(workspace: Workspace, job: Job | None = None) -> list[LimitView]:
    views: list[LimitView] = []
    for limit, spec in LIMIT_SPECS.items():
        if job is None:
            override = getattr(workspace, limit)
            inherited = server_default(limit)
        else:
            override = getattr(job, limit)
            inherited = workspace_value(workspace, limit)
        views.append(
            LimitView(
                key=str(limit),
                label=spec.label,
                help=spec.help,
                unit=str(spec.unit),
                value=capped(limit, override) if override is not None else inherited,
                override=override,
                inherited=inherited,
                minimum=spec.minimum,
                maximum=spec.ceiling(),
            )
        )
    return views


def validate_overrides(values: Mapping[str, int | None]) -> dict[str, int | None]:
    """Check owner input against the server bounds. Unknown keys are refused."""
    unknown = set(values) - {str(limit) for limit in RunLimit}
    if unknown:
        raise LimitError(f"Unknown limit: {', '.join(sorted(unknown))}.")
    checked: dict[str, int | None] = {}
    for key, value in values.items():
        spec = LIMIT_SPECS[RunLimit(key)]
        if value is not None and not spec.minimum <= value <= spec.ceiling():
            raise LimitError(
                f"{spec.label} must be between {spec.minimum:,} and "
                f"{spec.ceiling():,}.",
                ceiling_env=spec.ceiling_env,
            )
        checked[key] = value
    return checked


def save_overrides(target: RunLimitOverrides, values: Mapping[str, int | None]) -> None:
    checked = validate_overrides(values)
    for key, value in checked.items():
        setattr(target, key, value)
    target.save(update_fields=[*checked, "updated_at"])


def plan_limit(plan: Mapping[str, Any], limit: RunLimit) -> int:
    """A limit frozen into an execution plan.

    Plans made before a limit was frozen fall back to the server default.
    """
    value = ((plan.get("agent") or {}).get("limits") or {}).get(str(limit))
    return int(value) if isinstance(value, int) else server_default(limit)


def raise_hint(limit: RunLimit, value: int) -> str:
    """How to allow more than `value`, for messages about a stopped run.

    The message is stored with the run and shown to every member, so it does
    not name the server variable. Only staff can set it.
    """
    if value < LIMIT_SPECS[limit].ceiling():
        return "Raise it in the job's limits."
    return "That is the server maximum. Whoever runs this server can raise it."
