import logging
import threading

import djclick as click
from dbos import DBOS, DBOSClient
from django.conf import settings

from secondshift.automations.dbos_runtime import launch_dbos_worker, new_dbos_client
from secondshift.automations.periodic import PeriodicTask
from secondshift.automations.reconciliation import reconcile_terminal_workflows
from secondshift.automations.retention import prune_runs
from secondshift.automations.scheduling import synchronize_schedules
from secondshift.repositories.autosync import server_syncs, sync_moved_repositories

logger = logging.getLogger(__name__)

PRUNE_INTERVAL_SECONDS = 24 * 60 * 60


def worker_tasks(client: DBOSClient) -> list[PeriodicTask]:
    """What the worker repeats, in the order it runs them on each pass."""

    def reconcile() -> None:
        if count := reconcile_terminal_workflows(client):
            logger.warning("reconciled %s run(s) from terminal DBOS workflows", count)

    def schedules() -> None:
        logger.debug("synchronized %s DBOS schedule(s)", synchronize_schedules(client))

    def prune() -> None:
        logger.info("pruned %s run(s) past retention", prune_runs(client=client))

    def repositories() -> None:
        if synced := sync_moved_repositories():
            logger.info("synced %s repository(s) with new commits", synced)

    return [
        PeriodicTask("reconcile runs", every=0, run=reconcile),
        PeriodicTask("sync schedules", every=0, run=schedules),
        PeriodicTask("prune runs", every=PRUNE_INTERVAL_SECONDS, run=prune),
        PeriodicTask(
            "sync repositories",
            every=settings.REPOSITORY_SYNC_INTERVAL_SECONDS,
            run=repositories,
            enabled=server_syncs,
        ),
    ]


@click.command()
@click.option("--sync-interval", type=int, default=15, show_default=True)
def command(sync_interval: int) -> None:
    """Run DBOS workers and reconcile database-backed schedules."""
    launch_dbos_worker()
    click.echo("DBOS worker started")
    stopped = threading.Event()
    client = None
    try:
        client = new_dbos_client()
        tasks = worker_tasks(client)
        while True:
            for task in tasks:
                task.run_if_due()
            if stopped.wait(sync_interval):
                break
    except KeyboardInterrupt:
        click.echo("Stopping DBOS worker")
    finally:
        if client is not None:
            client.destroy()
        DBOS.destroy(workflow_completion_timeout_sec=10)
