import logging
import signal
import threading
from types import FrameType

import djclick as click
from dbos import DBOS, DBOSClient
from django.conf import settings

from secondshift.jobs.scheduling import synchronize_schedules
from secondshift.mcp_server.audit import prune_tool_calls
from secondshift.repositories.autosync import server_syncs, sync_moved_repositories
from secondshift.runs.orchestration.dbos_runtime import (
    launch_dbos_worker,
    new_dbos_client,
)
from secondshift.runs.orchestration.periodic import PeriodicTask
from secondshift.runs.orchestration.reconciliation import reconcile_terminal_workflows
from secondshift.runs.retention import prune_runs

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
        logger.info("pruned %s MCP tool call(s) past retention", prune_tool_calls())

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
    stopped = threading.Event()

    def stop(signum: int, frame: FrameType | None) -> None:
        stopped.set()

    previous = signal.signal(signal.SIGTERM, stop)
    client = None
    try:
        launch_dbos_worker()
        click.echo("DBOS worker started")
        client = new_dbos_client()
        tasks = worker_tasks(client)
        while not stopped.is_set():
            for task in tasks:
                if stopped.is_set():
                    break
                task.run_if_due()
            if stopped.wait(sync_interval):
                break
    except KeyboardInterrupt:
        click.echo("Stopping DBOS worker")
    finally:
        try:
            if client is not None:
                client.destroy()
        finally:
            try:
                DBOS.destroy(workflow_completion_timeout_sec=10)
            finally:
                signal.signal(signal.SIGTERM, previous)
