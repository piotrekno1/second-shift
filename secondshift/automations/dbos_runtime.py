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
    import_module("secondshift.automations.workflows")

    DBOS(config=dbos_config())
    DBOS.listen_queues([settings.DBOS_QUEUE_NAME])
    DBOS.launch()
    DBOS.register_queue(
        settings.DBOS_QUEUE_NAME,
        worker_concurrency=settings.DBOS_WORKER_CONCURRENCY,
    )
