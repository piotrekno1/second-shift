import ipaddress
import os
import socket
import subprocess
import sys
import webbrowser
from importlib.metadata import version

import djclick as click
from django.conf import settings
from django.core.management import call_command

from secondshift.config.constants import AppEnv, Environment, Service
from secondshift.core import launcher, local_agents, local_output, terminal
from secondshift.core.signin import SIGN_IN_LINK_SECONDS, sign_in_url
from secondshift.workspaces.owners import (
    ensure_owner,
    get_owner,
    has_owner,
    set_owner_credentials,
)


@click.command()
@click.option(
    "--host", default=None, help="Web listen host. Overrides SECONDSHIFT_HTTP_BIND."
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Web listen port. Overrides SECONDSHIFT_HTTP_BIND.",
)
@click.option(
    "--email",
    envvar="SECONDSHIFT_OWNER_EMAIL",
    help="Owner email. Needed only to share on the network with --host.",
)
@click.option(
    "--password",
    envvar="SECONDSHIFT_OWNER_PASSWORD",
    help="Owner password. Needed only to share on the network with --host.",
)
@click.option(
    "--browser/--no-browser",
    default=True,
    show_default=True,
    help="Open the sign-in link in your browser once started.",
)
@click.option(
    "--agents/--no-agents",
    default=True,
    show_default=True,
    help="Let jobs run through Claude Code or Codex subscriptions.",
)
@click.option("--debug", is_flag=True, help="Stream service diagnostics to stderr.")
def command(
    host: str | None,
    port: int | None,
    email: str | None,
    password: str | None,
    browser: bool,
    agents: bool,
    debug: bool,
) -> None:
    """Start SecondShift on this computer."""
    if settings.ENVIRONMENT != Environment.LOCAL:
        raise click.ClickException(
            "secondshift up runs with SECONDSHIFT_ENV=local. "
            "In a checkout, use process-compose instead."
        )
    try:
        with local_output.instance_lock():
            start(host, port, email, password, browser, agents, debug)
    except local_output.AlreadyRunning as error:
        raise click.ClickException(
            "SecondShift is already running or starting. Run secondshift status."
        ) from error


def start(
    host: str | None,
    port: int | None,
    email: str | None,
    password: str | None,
    browser: bool,
    agents: bool,
    debug: bool,
) -> None:
    parent = dict(os.environ)
    if debug:
        parent[AppEnv.LOG_LEVEL] = "DEBUG"
    configured_host, configured_port = launcher.web_address("127.0.0.1", 8000, parent)
    host, port = host or configured_host, port if port is not None else configured_port
    parent.pop(AppEnv.HTTP_BIND, None)
    terminal.heading(f"SecondShift {version('secondshift')}")
    check_port(host, port)
    check_docker()
    terminal.done("Docker is ready")
    if not (settings.WEB_DIR / "index.html").exists():
        raise click.ClickException(
            "The dashboard isn't built. In a checkout, run "
            "npm --prefix dashboard run build first."
        )

    call_command("migrate", interactive=False, verbosity=0)
    terminal.done("Local data is ready", str(settings.DATA_DIR))
    agent_host = None
    if agents:
        try:
            local_agents.prepare_agent_image(settings.AGENT_IMAGE, terminal.note)
            agent_host = local_agents.container_gateway_host()
            terminal.done("Agent image is ready")
        except local_agents.AgentSetupError as error:
            raise click.ClickException(
                f"{error} Run with --no-agents to start without agent subscriptions."
            ) from error
    else:
        # An empty value overrides the local default, in every process.
        parent[AppEnv.AGENT_IMAGE] = ""
    services = launcher.services(host, port, parent, agent_host=agent_host)
    runner = next(service for service in services if service.name == Service.RUNNER)
    prepare_runner(runner, debug)
    prepare_owner(host, email, password)

    url = launcher.public_url(host, port)

    def ready() -> None:
        link = sign_in_url(url, get_owner())
        minutes = SIGN_IN_LINK_SECONDS // 60
        terminal.done("All services are ready")
        click.echo()
        click.secho("SecondShift is running at ", fg="green", bold=True, nl=False)
        click.echo(terminal.link(url))
        if browser and webbrowser.open(link):
            click.echo("Opening your dashboard...")
        else:
            click.echo(f"Sign in with this link (valid for {minutes} minutes):")
            click.echo(f"  {terminal.link(link)}")
        click.echo()
        terminal.note("Keep this terminal open. Press Ctrl+C to stop.")
        terminal.note("Logs: secondshift logs --follow")

    terminal.note("Starting the web app, model gateway, runner and worker...")
    code = launcher.run(services, ready, click.echo, debug=debug, url=url)
    if code:
        click.echo(local_output.LOGS_HINT, err=True)
    sys.exit(code)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_docker() -> None:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=15, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode:
        raise click.ClickException(
            "Docker isn't running. Skills run in Docker sandboxes. "
            "Start Docker and try again."
        )


def prepare_runner(runner: launcher.Process, debug: bool) -> None:
    migration = subprocess.Popen(
        [sys.executable, "-m", "secondshift", "migrate", "--verbosity", "0"],
        env=runner.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert migration.stdout is not None
    reader = local_output.capture(migration.stdout, local_output.SETUP_LOG, debug)
    try:
        code = migration.wait()
    except KeyboardInterrupt:
        migration.terminate()
        migration.wait()
        raise
    finally:
        reader.join(timeout=2)
    if code:
        raise click.ClickException(
            "Couldn't prepare runner data. Run secondshift logs --service setup."
        )


def prepare_owner(host: str, email: str | None, password: str | None) -> None:
    if not has_owner():
        ensure_owner()
    if not is_loopback(host) and not get_owner().has_usable_password():
        # Other machines can reach the app, so a password must guard it.
        click.echo("Sharing on the network needs an owner email and password.")
        set_owner_credentials(
            email or click.prompt("Owner email"),
            password
            or click.prompt(
                "Owner password", hide_input=True, confirmation_prompt=True
            ),
        )


def check_port(host: str, port: int) -> None:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        family, kind, protocol, _, address = addresses[0]
        with socket.socket(family, kind, protocol) as listener:
            # The server binds the same way. Without this, closed connections
            # from the last run keep the port looking busy for a minute.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(address)
    except OSError as error:
        raise click.ClickException(
            f"Couldn't listen on {host}:{port}: {error.strerror or error}. "
            "Try secondshift up --port with an available port."
        ) from error
