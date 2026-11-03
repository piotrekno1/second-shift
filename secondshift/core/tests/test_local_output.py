import fcntl
import io
import json
import os
import sys
from pathlib import Path

import click
import pytest
from django.core.management import call_command
from django.test import override_settings

from secondshift.config.constants import Service
from secondshift.core import launcher, local_output
from secondshift.core.management.commands.logs import read_new


def test_service_output_is_saved_and_only_streamed_when_requested(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with override_settings(DATA_DIR=tmp_path):
        reader = local_output.capture(io.BytesIO(b"diagnostic\n"), Service.WEB, False)
        reader.join()
        assert capsys.readouterr().err == ""
        saved = (tmp_path / "logs/web.log").read_text()
        assert "[web] diagnostic" in saved
        reader = local_output.capture(io.BytesIO(b"visible\n"), Service.WEB, True)
        reader.join()
        assert "[web] visible" in capsys.readouterr().err
        call_command("logs", service="web", lines=1)
        assert "visible" in capsys.readouterr().out


def test_status_ignores_stale_state_without_active_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / local_output.LOCK_FILE).touch()
    (tmp_path / local_output.STATE_FILE).write_text(json.dumps({"url": "http://stale"}))
    with override_settings(DATA_DIR=tmp_path):
        call_command("status")
    output = capsys.readouterr().out
    assert "stopped" in output
    assert "stale" not in output


def test_duplicate_up_is_rejected_before_setup(tmp_path: Path) -> None:
    with (
        override_settings(DATA_DIR=tmp_path, ENVIRONMENT="local"),
        (tmp_path / local_output.LOCK_FILE).open("a") as lock,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(click.ClickException, match="already running"):
            call_command("up")


def test_worker_must_signal_readiness(tmp_path: Path) -> None:
    worker = launcher.Process(
        Service.WORKER, [sys.executable, "-c", "import time; time.sleep(60)"], {}
    )
    messages: list[str] = []
    ready: list[bool] = []
    with override_settings(DATA_DIR=tmp_path):
        code = launcher.run(
            [worker], lambda: ready.append(True), messages.append, start_timeout=0
        )
    assert code == 1
    assert ready == []
    assert "Timed out waiting for: worker." in messages


def test_logs_are_rotated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_output, "LOG_BYTES", 100)
    with override_settings(DATA_DIR=tmp_path):
        reader = local_output.capture(
            io.BytesIO(b"some diagnostic output\n" * 40), Service.WEB, False
        )
        reader.join()
    files = list((tmp_path / "logs").iterdir())
    assert len(files) == local_output.LOG_BACKUPS + 1


def test_follow_reads_new_file_after_rotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "web.log"
    path.write_text("first\n")
    position = read_new(path, None)
    assert capsys.readouterr().out == "first\n"
    path.rename(tmp_path / "web.log.1")
    path.write_text("second\n")
    read_new(path, position)
    assert capsys.readouterr().out == "second\n"


def test_status_uses_saved_custom_url_and_service_health(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "url": "http://127.0.0.1:9444",
        "services": {
            "web": {"pid": os.getpid(), "health_url": "http://127.0.0.1:9444/-/health/"}
        },
    }
    (tmp_path / local_output.STATE_FILE).write_text(json.dumps(state))
    monkeypatch.setattr(launcher, "is_healthy", lambda url: True)
    with (
        override_settings(DATA_DIR=tmp_path),
        (tmp_path / local_output.LOCK_FILE).open("a") as lock,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        call_command("status")
    output = capsys.readouterr().out
    assert "http://127.0.0.1:9444" in output
    assert "web: ready" in output
