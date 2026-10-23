import djclick as click

from secondshift.automations.retention import prune_runs


@click.command()
@click.option(
    "--days", type=int, default=None, help="Override SECONDSHIFT_RUN_RETENTION_DAYS."
)
def command(days: int | None) -> None:
    """Delete finished runs past retention, with their output and DBOS records."""
    click.echo(f"Pruned {prune_runs(days)} run(s)")
