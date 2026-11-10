import djclick as click
from django.conf import settings

from secondshift.core import local_output
from secondshift.core.signin import SIGN_IN_LINK_SECONDS, sign_in_url
from secondshift.workspaces.owners import get_owner, has_owner


@click.command()
@click.option(
    "--url",
    default=None,
    help="Where SecondShift is running. Defaults to where secondshift up serves it.",
)
def command(url: str | None) -> None:
    """Print a one-time link that signs you in as the owner."""
    if not has_owner():
        raise click.ClickException("There is no owner yet. Run secondshift up first.")
    state = local_output.load_state()
    base = url or (state["url"] if state else settings.FRONTEND_URL)
    minutes = SIGN_IN_LINK_SECONDS // 60
    click.echo(f"This link works once, within {minutes} minutes:")
    click.echo(sign_in_url(base, get_owner()))
