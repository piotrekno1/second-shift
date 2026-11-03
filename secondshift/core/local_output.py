"""Bounded, private diagnostics for the local supervisor."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Thread
from typing import BinaryIO, TextIO

import click
from django.conf import settings

LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3
STATE_FILE = "local-state.json"
LOCK_FILE = "local.lock"
WORKER_READY_FILE = "worker-ready"
SETUP_LOG = "setup"


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self) -> TextIO:
        stream = super()._open()
        Path(self.baseFilename).chmod(0o600)
        return stream


def log_directory() -> Path:
    directory = Path(settings.DATA_DIR) / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def capture(stream: BinaryIO, service: str, debug: bool) -> Thread:
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
