"""Inspect local service diagnostics."""

import time
from collections import deque
from pathlib import Path

import djclick as click

from secondshift.core.local_output import LOG_NAMES, log_directory


@click.command()
@click.option("--service", type=click.Choice(LOG_NAMES))
@click.option("--lines", default=50, type=click.IntRange(1, 10000), show_default=True)
@click.option("--follow", is_flag=True, help="Stream new log entries until Ctrl+C.")
def command(service: str | None, lines: int, follow: bool) -> None:
    """Show saved local service logs."""
    names = [service] if service else LOG_NAMES
    paths = [log_directory() / f"{name}.log" for name in names]
    positions = {path: print_tail(path, lines) for path in paths if path.exists()}
    if not positions and not follow:
        click.echo("No local logs yet. Run secondshift up first.")
    try:
        while follow:
            for path in paths:
                position = read_new(path, positions.get(path))
                if position is not None:
                    positions[path] = position
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass


def print_tail(path: Path, lines: int) -> tuple[int, int]:
    """Print the last lines of a log and return where reading stopped."""
    with path.open(errors="replace") as stream:
        for line in deque(stream, maxlen=lines):
            click.echo(line, nl=False)
        return path.stat().st_ino, stream.tell()


def read_new(path: Path, position: tuple[int, int] | None) -> tuple[int, int] | None:
    """Print lines added since position, starting over if the file rotated.

    A position is the file's inode and the offset already printed.
    """
    try:
        with path.open(errors="replace") as stream:
            stat = path.stat()
            inode, offset = position or (stat.st_ino, 0)
            if inode != stat.st_ino or stat.st_size < offset:
                offset = 0
            stream.seek(offset)
            for line in stream:
                click.echo(line, nl=False)
            return stat.st_ino, stream.tell()
    except FileNotFoundError:
        return position
