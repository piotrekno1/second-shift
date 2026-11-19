import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import FrameType

import djclick as click
from dbos import DBOS, DBOSClient
from django.conf import settings

from secondshift.connections.agents import retry_credential_cleanups
from secondshift.connections.mcp.client import refresh_built_in_tools
from secondshift.jobs.autosync import request_moved_syncs, server_syncs
from secondshift.jobs.repository_sync import run_requested_syncs
from secondshift.mcp_server.audit import prune_tool_calls
from secondshift.repositories.github.check import check_due_sources
from secondshift.repositories.sources import prune_source_cache
from secondshift.runs.orchestration.dbos_runtime import (
    launch_dbos_worker,
    new_dbos_client,
)
from secondshift.runs.orchestration.dispatch import (
    dispatch_waiting_tasks,
    recover_unqueued_runs,
)
from secondshift.runs.orchestration.durable import refresh_connections
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
# How often the loop wakes. Requested syncs start within this, and the other
# tasks wait for their own interval.
TICK_SECONDS = 1


def remove_cached_logins() -> None:
    if removed := retry_credential_cleanups():
        logger.info("removed %s cached agent login(s) from the runner", removed)


def find_new_commits() -> None:
    if requested := request_moved_syncs():
        logger.info("found new commits in %s repository(s)", requested)


def run_syncs() -> None:
    if synced := run_requested_syncs():
        logger.info("synced %s repository(s)", synced)


def worker_tasks(client: DBOSClient, every_pass: float) -> list[PeriodicTask]:
    """What the worker repeats, in the order it runs them on each pass.

    `every_pass` is the interval of the tasks that keep runs moving.
    """

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

    def sources() -> None:
        if checked := check_due_sources():
            logger.info("checked %s GitHub connection(s)", checked)

    return [
        PeriodicTask("reconcile runs", every=every_pass, run=reconcile),
        PeriodicTask(
            "dispatch waiting tasks", every=every_pass, run=dispatch_waiting_tasks
        ),
        PeriodicTask(
            "queue unqueued runs", every=every_pass, run=recover_unqueued_runs
        ),
        PeriodicTask("sync schedules", every=every_pass, run=schedules),
        PeriodicTask("prune runs", every=PRUNE_INTERVAL_SECONDS, run=prune),
        PeriodicTask(
            "find new commits",
            every=settings.REPOSITORY_SYNC_INTERVAL_SECONDS,
            run=find_new_commits,
            enabled=server_syncs,
        ),
        # Resync, adding a repository and new commits all ask for a sync.
        PeriodicTask("run requested syncs", every=0, run=run_syncs),
        PeriodicTask(
            "check GitHub connections",
            every=SOURCE_CHECK_POLL_SECONDS,
            run=sources,
        ),
        PeriodicTask(
            "refresh built-in MCP tools",
            every=SOURCE_CHECK_POLL_SECONDS,
            run=refresh_built_in_tools,
        ),
        PeriodicTask(
            "remove cached agent logins",
            every=CREDENTIAL_CLEANUP_SECONDS,
            run=remove_cached_logins,
        ),
    ]


class StopWorker(BaseException):
    """Raised on the main thread by SIGTERM, so blocking work ends right away.

    It is a BaseException so periodic tasks that catch Exception can't keep it.
    """


def stop(signum: int, frame: FrameType | None) -> None:
    # A handler must not take a lock. Setting a threading.Event does, and a
    # second SIGTERM arriving inside that call deadlocked the worker, holding
    # a repository's mirror lock. process-compose and uv run both send one.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise StopWorker


def end_running_workflows(before: set[threading.Thread]) -> None:
    """End the process if workflows still run after DBOS stopped waiting.

    Python would wait for their threads, which can take as long as a run.
    Meanwhile a new worker resumes the same workflows, so both would run
    them. DBOS keeps each finished step, so the next worker picks up from
    there.
    """
    left = [
        thread
        for thread in threading.enumerate()
        if thread.is_alive() and not thread.daemon and thread not in before
    ]
    if not left:
        return
    logger.warning(
        "Stopping with %d workflow threads still running. The next worker "
        "resumes their workflows.",
        len(left),
    )
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if sys.exc_info()[0] else 0)


@click.command()
@click.option("--sync-interval", type=int, default=15, show_default=True)
@click.option("--ready-file", type=click.Path(path_type=Path), hidden=True)
def command(sync_interval: int, ready_file: Path | None) -> None:
    """Run DBOS workers and reconcile database-backed schedules."""
    previous = signal.signal(signal.SIGTERM, stop)
    before = set(threading.enumerate())
    client = None
    try:
        launch_dbos_worker()
        click.echo("DBOS worker started")
        client = new_dbos_client()
        tasks = worker_tasks(client, every_pass=sync_interval)
        if ready_file is not None:
            ready_file.touch(mode=0o600)
        while True:
            # The periodic tasks run on this thread, outside any request.
            refresh_connections()
            for task in tasks:
                task.run_if_due()
            time.sleep(TICK_SECONDS)
    except (KeyboardInterrupt, StopWorker):
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
                end_running_workflows(before)
