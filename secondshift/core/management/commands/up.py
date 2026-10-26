import ipaddress
import os
import subprocess
import sys
import webbrowser

import djclick as click
from django.conf import settings
from django.core.management import call_command

from secondshift.config.constants import (
    Environment,
    Service,
)
from secondshift.core import launcher
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
def command(
    host: str | None,
    port: int | None,
    email: str | None,
    password: str | None,
    browser: bool,
) -> None:
    """Start SecondShift on this computer."""
    if settings.ENVIRONMENT != Environment.LOCAL:
        raise click.ClickException(
            "secondshift up runs with SECONDSHIFT_ENV=local. "
            "In a checkout, use process-compose instead."
        )
    parent = dict(os.environ)
    configured_host, configured_port = launcher.web_address("127.0.0.1", 8000, parent)
    host, port = host or configured_host, port if port is not None else configured_port
    parent.pop("SECONDSHIFT_HTTP_BIND", None)
    check_docker()
    if not (settings.WEB_DIR / "index.html").exists():
        raise click.ClickException(
            "The dashboard isn't built. In a checkout, run "
            "npm --prefix dashboard run build first."
        )

    click.echo(f"Data folder: {settings.DATA_DIR}")
    call_command("migrate", interactive=False, verbosity=0)
    services = launcher.services(host, port, parent)
    runner = next(service for service in services if service.name == Service.RUNNER)
    subprocess.run(
        [sys.executable, "-m", "secondshift", "migrate", "--verbosity", "0"],
        env=runner.env,
        check=True,
    )
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

    url = launcher.public_url(host, port)

    def ready() -> None:
        link = sign_in_url(url, get_owner())
        click.secho(f"SecondShift is running at {url}", fg="green", bold=True)
        minutes = SIGN_IN_LINK_SECONDS // 60
        click.echo(f"Sign in with this link. It works once, within {minutes} minutes:")
        click.echo(f"  {link}")
        click.echo("Run `secondshift login` for a new link. Press Ctrl+C to stop.")
        if browser:
            webbrowser.open(link)

    click.echo("Starting the web app, model gateway, runner and worker...")
    sys.exit(launcher.run(services, ready, click.echo))


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
