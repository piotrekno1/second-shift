import os
import signal
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


def test_dbos_cleanup_runs_if_client_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    client.destroy.side_effect = RuntimeError("cleanup failed")
    destroy = Mock()
    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(worker, "launch_dbos_worker", Mock())
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
