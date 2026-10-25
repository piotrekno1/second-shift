import logging
import threading
import time

import djclick as click
from dbos import DBOS
from django.conf import settings

from secondshift.automations.dbos_runtime import launch_dbos_worker, new_dbos_client
from secondshift.automations.reconciliation import reconcile_terminal_workflows
from secondshift.automations.retention import prune_runs
from secondshift.automations.scheduling import synchronize_schedules
from secondshift.repositories.autosync import server_syncs, sync_moved_repositories

logger = logging.getLogger(__name__)

PRUNE_INTERVAL_SECONDS = 24 * 60 * 60


@click.command()
@click.option("--sync-interval", type=int, default=15, show_default=True)
def command(sync_interval: int) -> None:  # noqa: C901
    """Run DBOS workers and reconcile database-backed schedules."""
    launch_dbos_worker()
    click.echo("DBOS worker started")
    stopped = threading.Event()
    client = None
    last_prune: float | None = None
    last_repository_sync: float | None = None
    repository_interval = settings.REPOSITORY_SYNC_INTERVAL_SECONDS
    try:
        client = new_dbos_client()
        while True:
            try:
                reconciled = reconcile_terminal_workflows(client)
                if reconciled:
                    logger.warning(
                        "reconciled %s run(s) from terminal DBOS workflows",
                        reconciled,
                    )
            except Exception:
                logger.exception("failed to reconcile terminal DBOS workflows")
            try:
                count = synchronize_schedules(client)
                logger.debug("synchronized %s DBOS schedule(s)", count)
            except Exception:
                logger.exception("failed to synchronize DBOS schedules")
            if (
                last_prune is None
                or time.monotonic() - last_prune >= PRUNE_INTERVAL_SECONDS
            ):
                last_prune = time.monotonic()
                try:
                    logger.info(
                        "pruned %s run(s) past retention", prune_runs(client=client)
                    )
                except Exception:
                    logger.exception("failed to prune old runs")
            if server_syncs() and (
                last_repository_sync is None
                or time.monotonic() - last_repository_sync >= repository_interval
            ):
                last_repository_sync = time.monotonic()
                try:
                    synced = sync_moved_repositories()
                    if synced:
                        logger.info("synced %s repository(s) with new commits", synced)
                except Exception:
                    logger.exception("failed to sync repositories")
            if stopped.wait(sync_interval):
                break
    except KeyboardInterrupt:
        click.echo("Stopping DBOS worker")
    finally:
        if client is not None:
            client.destroy()
        DBOS.destroy(workflow_completion_timeout_sec=10)
