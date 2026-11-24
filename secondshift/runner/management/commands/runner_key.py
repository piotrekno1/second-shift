"""Print this runner's provisioning key fingerprint."""

import djclick as click

from secondshift.runner.agent_credentials import provisioning_public_key
from secondshift.runner_protocol.credentials import key_fingerprint


@click.command()
def command() -> None:
    """Show the fingerprint the control plane pins for this runner.

    Compare it with `secondshift runners add` or `runners list` before trusting
    a runner with subscription logins.
    """
    click.echo(key_fingerprint(provisioning_public_key()))
