import djclick as click

from secondshift.repositories.sources import prune_source_cache


@click.command()
@click.option("--dry-run", is_flag=True, help="Show what would be removed.")
def command(dry_run: bool) -> None:
    """Remove cached repository sources no repository needs, and trim to size."""
    pruned = prune_source_cache(dry_run=dry_run)
    verb = "Would remove" if dry_run else "Removed"
    click.echo(
        f"{verb} {pruned.snapshots} snapshot(s) and {pruned.mirrors} mirror(s), "
        f"{pruned.bytes / (1024 * 1024):.1f} MiB"
    )
