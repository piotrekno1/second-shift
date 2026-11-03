"""Inspect the local supervisor without starting services."""

import os

import djclick as click

from secondshift.core import launcher, local_output


@click.command()
def command() -> None:
    """Show local service status and the dashboard address."""
    if not local_output.is_running():
        click.echo("SecondShift is stopped. Start it with secondshift up.")
        return
    state = local_output.load_state()
    if state is None:
        click.echo("SecondShift is starting.")
        return
    click.echo(f"Dashboard: {state['url']}")
    all_ready = True
    for name, service in state["services"].items():
        ready = is_alive(service["pid"]) and launcher.is_ready(
            name, service["health_url"]
        )
        all_ready &= ready
        click.echo(f"  {name}: {'ready' if ready else 'starting or unavailable'}")
    if not all_ready:
        click.echo(local_output.LOGS_HINT)
        raise SystemExit(1)


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True
