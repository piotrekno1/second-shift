"""Files the local supervisor shares with the logs and status commands.

Service output goes to bounded, private log files. A lock file marks a running
instance, and a state file records its dashboard address and processes.
"""

import fcntl
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Thread
from typing import Any, BinaryIO, TextIO

import click
from django.conf import settings

from secondshift.config.constants import Service

LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3
STATE_FILE = "local-state.json"
LOCK_FILE = "local.lock"
WORKER_READY_FILE = "worker-ready"
SETUP_LOG = "setup"
LOG_NAMES = [*(service.value for service in Service), SETUP_LOG]
LOGS_HINT = "Inspect diagnostics: secondshift logs --lines 50"


class AlreadyRunning(Exception):
    pass


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self) -> TextIO:
        stream = super()._open()
        Path(self.baseFilename).chmod(0o600)
        return stream


def state_path() -> Path:
    return settings.DATA_DIR / STATE_FILE


def worker_ready_path() -> Path:
    return settings.DATA_DIR / WORKER_READY_FILE


@contextmanager
def instance_lock() -> Iterator[None]:
    """Hold the lock that marks this instance as running."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (settings.DATA_DIR / LOCK_FILE).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AlreadyRunning from error
        # A crashed instance may have left its state behind.
        state_path().unlink(missing_ok=True)
        yield


def is_running() -> bool:
    lock_path = settings.DATA_DIR / LOCK_FILE
    if not lock_path.exists():
        return False
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def save_state(url: str, services: dict[str, dict[str, Any]]) -> None:
    path = state_path()
    path.touch(mode=0o600, exist_ok=True)
    path.write_text(json.dumps({"url": url, "services": services}))


def load_state() -> dict[str, Any] | None:
    try:
        return json.loads(state_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def log_directory() -> Path:
    directory = Path(settings.DATA_DIR) / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def capture(stream: BinaryIO, service: str, debug: bool) -> Thread:
    """Copy a process's output to its log file, and to stderr in debug mode."""
    path = log_directory() / f"{service}.log"
    path.touch(mode=0o600, exist_ok=True)
    handler = PrivateRotatingFileHandler(
        path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS
    )
    handler.setFormatter(logging.Formatter(f"%(asctime)s [{service}] %(message)s"))

    def consume() -> None:
        try:
            for line in iter(lambda: stream.readline(64 * 1024), b""):
                message = line.decode("utf-8", errors="replace").rstrip()
                handler.handle(
                    logging.LogRecord(service, logging.INFO, "", 0, message, (), None)
                )
                if debug:
                    click.echo(f"[{service}] {message}", err=True)
        finally:
            stream.close()
            handler.close()

    thread = Thread(target=consume, daemon=True)
    thread.start()
    return thread
