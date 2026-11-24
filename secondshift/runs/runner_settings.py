"""The settings page's view of the registered runners, for the people who run
the server.

It shows what `secondshift runners list` shows, without URLs or tokens.
"""

from datetime import datetime

from django.utils import timezone

from secondshift.runs import dao
from secondshift.runs.models import Runner
from secondshift.runs.runner_health import LOST_SECONDS
from secondshift.runs.runners import UNREACHABLE_SECONDS, fingerprint
from secondshift.workspaces.server_settings import SettingGroup, SettingValue, Tone

AGENTS_LABELS = {
    True: "Runs Claude Code and Codex.",
    False: "No agent image, so Claude Code and Codex runs placed here fail.",
    None: "Not known yet whether it runs Claude Code and Codex.",
}


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


def sessions_label(runner: Runner, unfinished: int) -> str:
    runs = "1 run" if unfinished == 1 else f"{unfinished} runs"
    if runner.max_sessions is None:
        return f"{runs}, no limit."
    return f"{runs} of {runner.max_sessions} at once."


def runner_item(runner: Runner, unfinished: int) -> SettingValue:
    offered = (
        "not listed yet"
        if runner.environments is None
        else ", ".join(runner.environments) or "none"
    )
    return SettingValue(
        runner.name,
        f"{runner.get_state_display()} · {health_label(runner)}",
        "",
        " ".join(
            [
                sessions_label(runner, unfinished),
                f"Environments: {offered}.",
                AGENTS_LABELS[runner.agents],
                f"Key {fingerprint(runner)}.",
            ]
        ),
        health_tone(runner),
    )


def runner_group() -> SettingGroup:
    runners = dao.all_runners()
    unfinished = dao.unfinished_run_counts([runner.pk for runner in runners])
    return SettingGroup(
        "Runners",
        [runner_item(runner, unfinished.get(runner.pk, 0)) for runner in runners]
        or [
            SettingValue(
                "Runners",
                "None registered",
                "",
                "Set SECONDSHIFT_RUNNER_SERVICE_URL or add one with "
                "secondshift runners add.",
            )
        ],
    )
