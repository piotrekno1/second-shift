"""Inspect the local supervisor without starting services."""

import fcntl
import json
import os

import djclick as click
from django.conf import settings

from secondshift.core import launcher, local_output


@click.command()
def command() -> None:
    """Show local service status and the dashboard address."""
    lock_path = settings.DATA_DIR / local_output.LOCK_FILE
    if not lock_path.exists():
        click.echo("SecondShift is stopped. Start it with secondshift up.")
        return
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            click.echo("SecondShift is stopped. Start it with secondshift up.")
            return
        try:
            state = json.loads(
                (settings.DATA_DIR / local_output.STATE_FILE).read_text()
            )
        except (FileNotFoundError, json.JSONDecodeError):
            click.echo("SecondShift is starting.")
            return
        click.echo(f"Dashboard: {state['url']}")
        unhealthy = False
        for name, service in state["services"].items():
            try:
                os.kill(service["pid"], 0)
                healthy = (
                    launcher.is_healthy(service["health_url"])
                    if service["health_url"]
                    else (settings.DATA_DIR / local_output.WORKER_READY_FILE).exists()
                )
            except (ProcessLookupError, PermissionError):
                healthy = False
            unhealthy |= not healthy
            click.echo(f"  {name}: {'ready' if healthy else 'starting or unavailable'}")
        if unhealthy:
            click.echo("Inspect diagnostics: secondshift logs --lines 50")
            raise SystemExit(1)
