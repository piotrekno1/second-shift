import os

import djclick as click

from secondshift.runs.dao import all_runners
from secondshift.runs.models import Runner
from secondshift.runs.runners import (
    DEFAULT_POOL,
    RunnerRegistryError,
    add_runner,
    change_runner_state,
    remove_runner,
    sync_configured_runner,
)


def refused(error: RunnerRegistryError) -> click.ClickException:
    return click.ClickException(str(error))


@click.group()
def command() -> None:
    """Register and manage the runners that execute runs.

    Every run still goes to the runner named default for now.
    """


@command.command("list")
def list_runners() -> None:
    """Show each registered runner. Tokens are never shown."""
    sync_configured_runner()
    runners = all_runners()
    if not runners:
        click.echo("No runners. Set SECONDSHIFT_RUNNER_SERVICE_URL or add one.")
        return
    for runner in runners:
        limit = runner.max_sessions or "no limit"
        click.echo(
            f"{runner.name}\t{runner.state}\tpool={runner.pool}\t"
            f"sessions={limit}\t{runner.url}"
        )


@command.command("add")
@click.argument("name")
@click.argument("url")
@click.option(
    "--token-env",
    metavar="VARIABLE",
    help="Read the runner's token from this environment variable. "
    "Otherwise you are asked for it.",
)
@click.option("--pool", default=DEFAULT_POOL, show_default=True)
@click.option("--max-sessions", type=click.IntRange(min=1), default=None)
def add(
    name: str,
    url: str,
    token_env: str | None,
    pool: str,
    max_sessions: int | None,
) -> None:
    """Register a runner at URL. Its token stays out of shell history."""
    if token_env:
        token = os.environ.get(token_env, "")
        if not token:
            raise click.ClickException(f"{token_env} is not set.")
    else:
        token = click.prompt("Runner token", hide_input=True)
    try:
        add_runner(name, url, token, pool, max_sessions)
    except RunnerRegistryError as error:
        raise refused(error) from None
    click.echo(f"Added runner {name}.")


@command.command("drain")
@click.argument("name")
def drain(name: str) -> None:
    """Stop placing new runs on a runner. Its runs finish."""
    try:
        change_runner_state(name, Runner.State.DRAINING)
    except RunnerRegistryError as error:
        raise refused(error) from None
    click.echo(f"Draining runner {name}.")


@command.command("activate")
@click.argument("name")
def activate(name: str) -> None:
    """Place new runs on a runner again."""
    try:
        change_runner_state(name, Runner.State.ACTIVE)
    except RunnerRegistryError as error:
        raise refused(error) from None
    click.echo(f"Runner {name} is active.")


@command.command("remove")
@click.argument("name")
def remove(name: str) -> None:
    """Forget a runner."""
    try:
        remove_runner(name)
    except RunnerRegistryError as error:
        raise refused(error) from None
    click.echo(f"Removed runner {name}.")
