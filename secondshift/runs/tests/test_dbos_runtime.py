from unittest.mock import Mock

import pytest
from django.test import override_settings

from secondshift.runs.orchestration import dbos_runtime


@pytest.fixture
def launch(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Record the order of DBOS calls without a database."""
    calls = Mock()
    client = Mock()
    calls.attach_mock(client.register_queue, "register_queue")
    calls.attach_mock(client.destroy, "destroy_client")
    dbos = Mock()
    calls.attach_mock(dbos, "dbos")
    monkeypatch.setattr(dbos_runtime, "DBOS", dbos)
    monkeypatch.setattr(dbos_runtime, "new_dbos_client", lambda: client)
    monkeypatch.setattr(dbos_runtime, "import_module", Mock())
    return calls


@override_settings(DBOS_WORKER_CONCURRENCY=1, DBOS_GLOBAL_CONCURRENCY=0)
def test_queue_limits_are_saved_before_the_queue_thread_starts(launch: Mock) -> None:
    dbos_runtime.launch_dbos_worker()

    names = [name for name, _, _ in launch.mock_calls]
    assert names == [
        "dbos.migrate",
        "register_queue",
        "destroy_client",
        "dbos",
        "dbos.listen_queues",
        "dbos.launch",
    ]
    # Registering after launch would save limits the queue thread never reads.
    assert not launch.dbos.register_queue.called
