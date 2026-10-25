from pathlib import Path

import djclick as click

from secondshift.jobs.scheduling import synchronize_schedules
from secondshift.jobs.sync import sync_manifest
from secondshift.repositories.models import Repository


@click.command()
@click.argument("repository_id", type=int)
@click.argument("manifest", type=click.Path(exists=True, path_type=Path))
def command(repository_id: int, manifest: Path) -> None:
    """Validate and synchronize one repository manifest."""
    repository = Repository.objects.get(pk=repository_id)
    jobs = sync_manifest(repository, manifest)
    synchronize_schedules()
    click.echo(f"Synchronized {len(jobs)} jobs for {repository.full_name}")
