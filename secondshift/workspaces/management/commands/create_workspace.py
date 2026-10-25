import djclick as click
from django.conf import settings
from django.utils.text import slugify

from secondshift.workspaces.invites import (
    INVITE_LIFETIME,
    WorkspaceTaken,
    invite_first_owner,
    invite_path,
)


@click.command()
@click.argument("name")
@click.option("--slug", help="Short name used in the database. From NAME by default.")
@click.option(
    "--url",
    default=settings.FRONTEND_URL,
    show_default=True,
    help="Where people open SecondShift.",
)
def command(name: str, slug: str | None, url: str) -> None:
    """Create a workspace and print a link that makes its first owner.

    Run it again for a workspace whose link expired before anyone used it.
    """
    slug = slug or slugify(name)
    if not slug:
        raise click.ClickException("Choose a --slug with letters or numbers.")
    try:
        new = invite_first_owner(name, slug)
    except WorkspaceTaken as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Workspace {new.invite.workspace.name} ({slug}) is ready.")
    click.echo(
        f"Open this link to sign in or sign up as its owner. "
        f"It works once, within {INVITE_LIFETIME.days} days:"
    )
    click.echo(f"  {url.rstrip('/')}{invite_path(new.token)}")
