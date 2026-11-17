from __future__ import annotations

from importlib import import_module

from dbos import DBOS, DBOSClient, DBOSConfig
from django.conf import settings


def dbos_config() -> DBOSConfig:
    return {
        "name": settings.DBOS_APPLICATION_NAME,
        "application_version": settings.DBOS_APPLICATION_VERSION,
        "system_database_url": settings.DBOS_SYSTEM_DATABASE_URL,
    }


def new_dbos_client() -> DBOSClient:
    return DBOSClient(
        system_database_url=settings.DBOS_SYSTEM_DATABASE_URL,
        application_name=settings.DBOS_APPLICATION_NAME,
    )


def launch_dbos_worker() -> None:
    # Importing registers decorated workflows before DBOS starts recovery.
    import_module("secondshift.runs.orchestration.workflows")

    if settings.DBOS_WORKER_CONCURRENCY < 1 or settings.DBOS_GLOBAL_CONCURRENCY < 0:
        raise ValueError(
            "Run concurrency must be positive; the global limit may be zero."
        )
    global_limit = settings.DBOS_GLOBAL_CONCURRENCY or None
    worker_limit = settings.DBOS_WORKER_CONCURRENCY
    if global_limit is not None:
        worker_limit = min(worker_limit, global_limit)
    # The queue thread reads the queue's limits once, when DBOS launches. So
    # they are saved first, or a changed limit would wait for the next start.
    # A client never creates the system database, so the migrations run first.
    DBOS.migrate(settings.DBOS_SYSTEM_DATABASE_URL)
    client = new_dbos_client()
    try:
        client.register_queue(
            settings.DBOS_QUEUE_NAME,
            worker_concurrency=worker_limit,
            global_concurrency=global_limit,
            on_conflict="always_update",
        )
    finally:
        client.destroy()
    DBOS(config=dbos_config())
    DBOS.listen_queues([settings.DBOS_QUEUE_NAME])
    DBOS.launch()
