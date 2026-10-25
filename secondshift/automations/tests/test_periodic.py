import pytest

from secondshift.automations.management.commands.run_dbos_worker import (
    PRUNE_INTERVAL_SECONDS,
    worker_tasks,
)
from secondshift.automations.periodic import PeriodicTask


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_task_runs_at_once_then_after_its_interval() -> None:
    clock, runs = Clock(), []
    task = PeriodicTask(
        "sync", every=300, run=lambda: runs.append(clock.now), clock=clock
    )

    task.run_if_due()
    clock.now += 299
    task.run_if_due()
    clock.now += 1
    task.run_if_due()

    assert runs == [1000.0, 1300.0]


def test_a_disabled_task_never_runs() -> None:
    runs: list[int] = []
    task = PeriodicTask(
        "sync", every=0, run=lambda: runs.append(1), enabled=lambda: False
    )

    task.run_if_due()

    assert runs == []


def test_a_failure_is_logged_and_waits_for_the_next_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock, calls = Clock(), []

    def fail() -> None:
        calls.append(clock.now)
        raise RuntimeError("boom")

    task = PeriodicTask("prune", every=60, run=fail, clock=clock)

    task.run_if_due()
    task.run_if_due()

    assert calls == [1000.0]
    assert "periodic task 'prune' failed" in caplog.text


def test_the_worker_syncs_repositories_only_when_the_server_does(
    settings: object,
) -> None:
    settings.LOCAL_REPOSITORIES_ENABLED = True  # type: ignore[attr-defined]
    settings.REPOSITORY_SYNC_INTERVAL_SECONDS = 300  # type: ignore[attr-defined]
    tasks = {task.name: task for task in worker_tasks(client=object())}  # type: ignore[arg-type]

    assert tasks["prune runs"].every == PRUNE_INTERVAL_SECONDS
    assert tasks["sync repositories"].every == 300
    assert tasks["sync repositories"].enabled() is True

    settings.REPOSITORY_SYNC_INTERVAL_SECONDS = 0  # type: ignore[attr-defined]
    assert tasks["sync repositories"].enabled() is False
