"""The settings page's view of the registered runners, for the people who run
the server.

It shows what `secondshift runners list` shows, without URLs or tokens, as one
card per runner.
"""

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from secondshift.runner_protocol.environments import environment_variable
from secondshift.runs import dao
from secondshift.runs.models import Runner
from secondshift.runs.runner_health import LOST_SECONDS
from secondshift.runs.runners import UNREACHABLE_SECONDS, fingerprint
from secondshift.workspaces.server_settings import Tone, image_label


def minutes_since(moment: datetime) -> str:
    minutes = max(1, int((timezone.now() - moment).total_seconds() // 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def health_label(runner: Runner) -> str:
    if runner.state == Runner.State.DOWN:
        return "not checked"
    if runner.failing_since is None:
        return "answering"
    silent = (timezone.now() - runner.failing_since).total_seconds()
    since = minutes_since(runner.failing_since)
    if silent >= LOST_SECONDS:
        return f"not answering for {since}, its runs failed"
    if silent >= UNREACHABLE_SECONDS:
        return f"not answering for {since}, takes no new runs"
    return f"missed a check {since} ago"


def health_tone(runner: Runner) -> Tone:
    if runner.state == Runner.State.DOWN or runner.failing_since is None:
        return Tone.NONE
    lost = (timezone.now() - runner.failing_since).total_seconds() >= LOST_SECONDS
    return Tone.DANGER if lost else Tone.ATTENTION


@dataclass(frozen=True)
class RunnerEnvironmentView:
    name: str
    # The image with a short digest. Empty until the runner lists its images.
    image: str
    # The variable that sets it on the runner.
    env: str
    default: bool


@dataclass(frozen=True)
class RunnerView:
    """One runner as the settings page shows it, without its URL or token."""

    name: str
    state: str
    # How its health checks go, such as "answering".
    health: str
    tone: Tone
    running: int
    max_sessions: int | None
    # None until the runner lists them.
    environments: list[RunnerEnvironmentView] | None
    # Whether it can run Claude Code and Codex. None until it says.
    agents: bool | None
    # Empty until its key is pinned.
    key_fingerprint: str


def runner_environment_views(runner: Runner) -> list[RunnerEnvironmentView] | None:
    if runner.images is not None:
        names = list(runner.images)
    elif runner.environments is not None:
        names = list(runner.environments)
    else:
        return None
    images = runner.images or {}
    return [
        RunnerEnvironmentView(
            name,
            image_label(images[name]) if name in images else "",
            environment_variable(name),
            name == runner.default_environment,
        )
        for name in names
    ]


def runner_view(runner: Runner, unfinished: int) -> RunnerView:
    return RunnerView(
        name=runner.name,
        state=runner.get_state_display(),
        health=health_label(runner),
        tone=health_tone(runner),
        running=unfinished,
        max_sessions=runner.max_sessions,
        environments=runner_environment_views(runner),
        agents=runner.agents,
        key_fingerprint=fingerprint(runner) if runner.provisioning_key else "",
    )


def runner_views() -> list[RunnerView]:
    runners = dao.all_runners()
    unfinished = dao.unfinished_run_counts([runner.pk for runner in runners])
    return [runner_view(runner, unfinished.get(runner.pk, 0)) for runner in runners]
