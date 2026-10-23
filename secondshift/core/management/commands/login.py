import djclick as click

from secondshift.core.owners import get_owner, has_owner
from secondshift.core.signin import SIGN_IN_LINK_SECONDS, sign_in_url


@click.command()
@click.option(
    "--url",
    default="http://127.0.0.1:8000",
    show_default=True,
    help="Where SecondShift is running.",
)
def command(url: str) -> None:
    """Print a one-time link that signs you in as the owner."""
    if not has_owner():
        raise click.ClickException("There is no owner yet. Run secondshift up first.")
    minutes = SIGN_IN_LINK_SECONDS // 60
    click.echo(f"This link works once, within {minutes} minutes:")
    click.echo(sign_in_url(url, get_owner()))
