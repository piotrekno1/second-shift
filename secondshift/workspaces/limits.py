"""Run limits: how far one run may go.

Each limit has a server default and a server maximum, both set through
environment variables. A workspace owner can choose a value for the workspace
and for each job, up to the maximum. A job's value wins over the workspace's,
and the workspace's wins over the default. Repository configuration cannot set
limits.

The values that apply are frozen into the run's execution plan when the run is
created. Changing a limit never affects a run that is already queued or running.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from django.conf import settings

from secondshift.workspaces.models import RunLimitOverrides, Workspace


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
        "Reading files, searching, running commands and calling connected services.",
        LimitUnit.COUNT,
        "AGENT_MAX_TOOL_CALLS",
        minimum=0,
    ),
    RunLimit.TOKEN_BUDGET: LimitSpec(
        "Tokens",
        "Input and output tokens. Cached input counts as a tenth. Model calls past this are refused.",
        LimitUnit.TOKENS,
        "MODEL_RUN_TOKEN_BUDGET",
        minimum=1_000,
    ),
    RunLimit.TIMEOUT_SECONDS: LimitSpec(
        "Time",
        "Counted from when the run starts. Waiting to start does not count.",
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


def workspace_limits(workspace: Workspace) -> dict[str, int]:
    """The limits a run gets from the workspace alone, for its execution plan."""
    return {str(limit): workspace_value(workspace, limit) for limit in RunLimit}


def limit_views(
    overrides: RunLimitOverrides, inherited: Callable[[RunLimit], int]
) -> list[LimitView]:
    """Each limit at one level: what it chose, and what applies without a choice."""
    views: list[LimitView] = []
    for limit, spec in LIMIT_SPECS.items():
        override = getattr(overrides, limit)
        fallback = inherited(limit)
        views.append(
            LimitView(
                key=str(limit),
                label=spec.label,
                help=spec.help,
                unit=str(spec.unit),
                value=capped(limit, override) if override is not None else fallback,
                override=override,
                inherited=fallback,
                minimum=spec.minimum,
                maximum=spec.ceiling(),
            )
        )
    return views


def workspace_limit_views(workspace: Workspace) -> list[LimitView]:
    return limit_views(workspace, server_default)


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


def raise_hint(limit: RunLimit, value: int) -> str:
    """How to allow more than `value`, for messages about a stopped run.

    The message is stored with the run and shown to every member, so it does
    not name the server variable. Only staff can set it.
    """
    if value < LIMIT_SPECS[limit].ceiling():
        return "Raise it in the job's limits."
    return "That is the server maximum. Whoever runs this server can raise it."
