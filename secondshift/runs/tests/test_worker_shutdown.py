import os
import signal
import threading
import time
from unittest.mock import Mock

import pytest
from django.core.management import call_command

from secondshift.runs.management.commands import run_dbos_worker as worker


def test_sigterm_stops_tasks_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Mock()
    destroy = Mock()
    following_task = Mock()
    stop_task = Mock()
    stop_task.run_if_due.side_effect = lambda: os.kill(os.getpid(), signal.SIGTERM)
    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(worker, "launch_dbos_worker", Mock())
    monkeypatch.setattr(worker, "refresh_connections", lambda: None)
    monkeypatch.setattr(worker, "new_dbos_client", lambda: client)
    monkeypatch.setattr(
        worker, "worker_tasks", lambda client: [stop_task, following_task]
    )
    monkeypatch.setattr(worker.DBOS, "destroy", destroy)

    call_command("run_dbos_worker", sync_interval=3600)

    stop_task.run_if_due.assert_called_once()
    following_task.run_if_due.assert_not_called()
    client.destroy.assert_called_once()
    destroy.assert_called_once_with(workflow_completion_timeout_sec=10)
    assert signal.getsignal(signal.SIGTERM) == previous


def test_sigterm_ends_a_task_blocked_on_a_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Like a sync waiting on a repository's mirror lock, or holding it.
    client = Mock()
    blocked = Mock()
    blocked.run_if_due.side_effect = lambda: (
        os.kill(os.getpid(), signal.SIGTERM),
        threading.Event().wait(30),
    )
    monkeypatch.setattr(worker, "launch_dbos_worker", Mock())
    monkeypatch.setattr(worker, "refresh_connections", lambda: None)
    monkeypatch.setattr(worker, "new_dbos_client", lambda: client)
    monkeypatch.setattr(worker, "worker_tasks", lambda client: [blocked])
    monkeypatch.setattr(worker.DBOS, "destroy", Mock())
    started = time.monotonic()

    call_command("run_dbos_worker", sync_interval=3600)

    assert time.monotonic() - started < 5
    client.destroy.assert_called_once()


def test_a_second_sigterm_during_cleanup_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # process-compose and uv run each send one.
    client = Mock()
    client.destroy.side_effect = lambda: os.kill(os.getpid(), signal.SIGTERM)
    destroy = Mock()
    stop_task = Mock()
    stop_task.run_if_due.side_effect = lambda: os.kill(os.getpid(), signal.SIGTERM)
    monkeypatch.setattr(worker, "launch_dbos_worker", Mock())
    monkeypatch.setattr(worker, "refresh_connections", lambda: None)
    monkeypatch.setattr(worker, "new_dbos_client", lambda: client)
    monkeypatch.setattr(worker, "worker_tasks", lambda client: [stop_task])
    monkeypatch.setattr(worker.DBOS, "destroy", destroy)

    call_command("run_dbos_worker", sync_interval=3600)

    destroy.assert_called_once_with(workflow_completion_timeout_sec=10)


def test_dbos_cleanup_runs_if_client_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    client.destroy.side_effect = RuntimeError("cleanup failed")
    destroy = Mock()
    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(worker, "launch_dbos_worker", Mock())
    monkeypatch.setattr(worker, "refresh_connections", lambda: None)
    monkeypatch.setattr(worker, "new_dbos_client", lambda: client)
    monkeypatch.setattr(worker, "worker_tasks", Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(worker.DBOS, "destroy", destroy)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        call_command("run_dbos_worker")

    destroy.assert_called_once_with(workflow_completion_timeout_sec=10)
    assert signal.getsignal(signal.SIGTERM) == previous


def test_startup_failure_restores_signal_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destroy = Mock()
    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(
        worker, "launch_dbos_worker", Mock(side_effect=RuntimeError("startup failed"))
    )
    monkeypatch.setattr(worker.DBOS, "destroy", destroy)

    with pytest.raises(RuntimeError, match="startup failed"):
        call_command("run_dbos_worker")

    destroy.assert_called_once_with(workflow_completion_timeout_sec=10)
    assert signal.getsignal(signal.SIGTERM) == previous


def test_workflows_still_running_after_destroy_end_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exits: list[int] = []
    monkeypatch.setattr(worker.os, "_exit", exits.append)
    before = set(worker.threading.enumerate())
    release = worker.threading.Event()
    # Stands in for a workflow thread waiting on a long agent run.
    workflow = worker.threading.Thread(target=release.wait)
    workflow.start()
    try:
        worker.end_running_workflows(before)
    finally:
        release.set()
        workflow.join()

    assert exits == [0]


def test_a_worker_with_no_workflows_left_exits_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exits: list[int] = []
    monkeypatch.setattr(worker.os, "_exit", exits.append)
    before = set(worker.threading.enumerate())
    daemon = worker.threading.Thread(target=worker.threading.Event().wait, daemon=True)
    daemon.start()

    worker.end_running_workflows(before)

    assert exits == []
