import os

import djclick as click

from secondshift.runs.dao import all_runners
from secondshift.runs.models import Runner
from secondshift.runs.runner_health import check_runner
from secondshift.runs.runners import (
    DEFAULT_POOL,
    RunnerRegistryError,
    add_runner,
    change_runner_state,
    fingerprint,
    remove_runner,
    sync_configured_runner,
    trust_current_key,
)


def refused(error: RunnerRegistryError) -> click.ClickException:
    return click.ClickException(str(error))


@click.group()
def command() -> None:
    """Register and manage the runners that execute runs.

    Runs go to active runners in the default pool.
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
        offered = (
            "not asked yet"
            if runner.environments is None
            else ",".join(runner.environments)
        )
        agents = {True: "yes", False: "no", None: "unknown"}[runner.agents]
        health = (
            "answering"
            if runner.failing_since is None
            else f"not answering since {runner.failing_since:%Y-%m-%d %H:%M:%S}"
        )
        click.echo(
            f"{runner.name}\t{runner.state}\t{health}\tpool={runner.pool}\t"
            f"sessions={limit}\tenvironments={offered}\tagents={agents}\t"
            f"key={fingerprint(runner)}\t{runner.url}"
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
@click.option(
    "--allow-http",
    is_flag=True,
    help="Accept an http:// URL. Only on a private network you trust, such as "
    "WireGuard: the token, repository files and output cross it unencrypted.",
)
def add(
    name: str,
    url: str,
    token_env: str | None,
    pool: str,
    max_sessions: int | None,
    allow_http: bool,
) -> None:
    """Register a runner at URL. Its token stays out of shell history."""
    if token_env:
        token = os.environ.get(token_env, "")
        if not token:
            raise click.ClickException(f"{token_env} is not set.")
    else:
        token = click.prompt("Runner token", hide_input=True)
    try:
        runner = add_runner(name, url, token, pool, max_sessions, allow_http)
    except RunnerRegistryError as error:
        raise refused(error) from None
    click.echo(f"Added runner {name}.")
    # Pin its key and learn its environments now, not on the worker's next pass.
    check_runner(runner)
    runner.refresh_from_db()
    click.echo(f"Provisioning key: {fingerprint(runner)}")
    if not runner.provisioning_key:
        click.echo(
            "Warning: the runner did not answer. Its key is pinned when it "
            "first answers. Check the fingerprint with secondshift runners list.",
            err=True,
        )
    elif runner.agents is False:
        click.echo(
            f"Warning: {name} has no agent image. Claude Code and Codex runs "
            "placed on it fail. Build the agent image on its host if "
            "workspaces use them.",
            err=True,
        )


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
@click.option(
    "--force",
    is_flag=True,
    help="Remove it even if it can't confirm it deleted its cached logins.",
)
def remove(name: str, force: bool) -> None:
    """Forget a runner, after it deletes its cached agent logins."""
    try:
        removed = remove_runner(name, force=force)
    except RunnerRegistryError as error:
        raise refused(error) from None
    click.echo(f"Removed runner {name}.")
    if not removed:
        click.echo(
            f"Warning: {name} may still hold agent logins. Delete its "
            "agent-auth directory and its backups on the host.",
            err=True,
        )


@command.command("trust-key")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Don't ask before pinning.")
def trust_key(name: str, yes: bool) -> None:
    """Pin the provisioning key a runner answers with now.

    Only after its key changed on purpose, such as when its agent-auth
    directory was replaced. Logins are sealed only to the pinned key.
    """
    if not yes:
        click.confirm(
            f"Trust whatever answers at runner {name}'s address with its logins?",
            abort=True,
        )
    try:
        old, new = trust_current_key(name)
    except RunnerRegistryError as error:
        raise refused(error) from None
    click.echo(f"Runner {name} key: {old} -> {new}")
