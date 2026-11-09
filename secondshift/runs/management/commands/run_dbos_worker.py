import logging
import signal
import threading
from pathlib import Path
from types import FrameType

import djclick as click
from dbos import DBOS, DBOSClient
from django.conf import settings

from secondshift.connections.agents import retry_credential_cleanups
from secondshift.jobs.autosync import server_syncs, sync_moved_repositories
from secondshift.mcp_server.audit import prune_tool_calls
from secondshift.repositories.github_check import check_due_sources
from secondshift.repositories.sources import prune_source_cache
from secondshift.runs.orchestration.dbos_runtime import (
    launch_dbos_worker,
    new_dbos_client,
)
from secondshift.runs.orchestration.dispatch import dispatch_waiting_tasks
from secondshift.runs.orchestration.periodic import PeriodicTask
from secondshift.runs.orchestration.reconciliation import reconcile_terminal_workflows
from secondshift.runs.orchestration.schedules import synchronize_schedules
from secondshift.runs.retention import prune_runs

logger = logging.getLogger(__name__)

PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
# How often to look for GitHub connections due for their daily check.
SOURCE_CHECK_POLL_SECONDS = 60 * 60
# How often to ask the runner again to remove logins it kept.
CREDENTIAL_CLEANUP_SECONDS = 60


def remove_cached_logins() -> None:
    if removed := retry_credential_cleanups():
        logger.info("removed %s cached agent login(s) from the runner", removed)


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
        cache = prune_source_cache()
        logger.info(
            "pruned %s snapshot(s) and %s mirror(s) from the source cache, %s bytes",
            cache.snapshots,
            cache.mirrors,
            cache.bytes,
        )

    def repositories() -> None:
        if synced := sync_moved_repositories():
            logger.info("synced %s repository(s) with new commits", synced)

    def sources() -> None:
        if checked := check_due_sources():
            logger.info("checked %s GitHub connection(s)", checked)

    return [
        PeriodicTask("reconcile runs", every=0, run=reconcile),
        PeriodicTask("dispatch waiting tasks", every=0, run=dispatch_waiting_tasks),
        PeriodicTask("sync schedules", every=0, run=schedules),
        PeriodicTask("prune runs", every=PRUNE_INTERVAL_SECONDS, run=prune),
        PeriodicTask(
            "sync repositories",
            every=settings.REPOSITORY_SYNC_INTERVAL_SECONDS,
            run=repositories,
            enabled=server_syncs,
        ),
        PeriodicTask(
            "check GitHub connections",
            every=SOURCE_CHECK_POLL_SECONDS,
            run=sources,
        ),
        PeriodicTask(
            "remove cached agent logins",
            every=CREDENTIAL_CLEANUP_SECONDS,
            run=remove_cached_logins,
        ),
    ]


@click.command()
@click.option("--sync-interval", type=int, default=15, show_default=True)
@click.option("--ready-file", type=click.Path(path_type=Path), hidden=True)
def command(sync_interval: int, ready_file: Path | None) -> None:
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
        if ready_file is not None:
            ready_file.touch(mode=0o600)
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
