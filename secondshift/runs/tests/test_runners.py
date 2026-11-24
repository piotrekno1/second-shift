from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection

from secondshift.runner.tests.fakes import ClosableFake
from secondshift.runner_protocol.sessions import CommandRequest, CommandResult
from secondshift.runs import runners
from secondshift.runs.dao import all_runners, find_runner, update_run
from secondshift.runs.execution.workspace_tools import execute_runner_command_step
from secondshift.runs.models import Run, Runner
from secondshift.runs.orchestration.workflows import stop_runner_session_step
from secondshift.runs.runners import (
    DEFAULT_RUNNER,
    RunnerRegistryError,
    add_runner,
    assign_runner,
    change_runner_state,
    remove_runner,
    runner_client,
    sync_configured_runner,
)
from secondshift.runs.tests.test_workflows import execution_run

TOKEN = "t" * 32
OTHER_TOKEN = "o" * 32


@pytest.fixture
def configured(settings: Any) -> Any:
    settings.RUNNER_SERVICE_URL = "http://runner-a:8002"
    settings.RUNNER_SERVICE_TOKEN = TOKEN
    return settings


@pytest.mark.django_db
def test_sync_registers_the_configured_runner_as_default(
    configured: Any,
) -> None:
    runner = sync_configured_runner()

    assert runner is not None
    assert runner.name == DEFAULT_RUNNER
    assert runner.url == "http://runner-a:8002"
    assert runner.token == TOKEN
    assert runner.state == Runner.State.ACTIVE


@pytest.mark.django_db
def test_sync_follows_settings_but_keeps_the_operator_state(
    configured: Any,
) -> None:
    sync_configured_runner()
    change_runner_state(DEFAULT_RUNNER, Runner.State.DRAINING)
    configured.RUNNER_SERVICE_URL = "http://runner-b:8002"
    configured.RUNNER_SERVICE_TOKEN = OTHER_TOKEN

    sync_configured_runner()

    runner = find_runner(DEFAULT_RUNNER)
    assert runner is not None
    assert (runner.url, runner.token) == ("http://runner-b:8002", OTHER_TOKEN)
    assert runner.state == Runner.State.DRAINING
    assert len(all_runners()) == 1


@pytest.mark.django_db
def test_sync_without_a_configured_runner_registers_nothing(
    settings: Any,
) -> None:
    settings.RUNNER_SERVICE_URL = ""

    assert sync_configured_runner() is None
    assert all_runners() == []


@pytest.mark.django_db
def test_the_token_is_encrypted_at_rest() -> None:
    runner = add_runner("runner-b", "http://runner-b:8002", TOKEN)

    with connection.cursor() as cursor:
        cursor.execute("SELECT token FROM runs_runner WHERE id = %s", [runner.pk])
        (stored,) = cursor.fetchone()
    assert TOKEN not in stored


@pytest.mark.django_db
def test_add_keeps_pool_and_session_limit() -> None:
    add_runner("gpu-1", "https://gpu-1.internal/", TOKEN, pool="gpu", max_sessions=4)

    runner = find_runner("gpu-1")
    assert runner is not None
    assert (runner.url, runner.pool, runner.max_sessions) == (
        "https://gpu-1.internal",
        "gpu",
        4,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "url", "token", "pool", "max_sessions"),
    [
        ("Runner B", "http://runner-b:8002", TOKEN, "default", None),
        ("runner-b", "runner-b:8002", TOKEN, "default", None),
        ("runner-b", "file:///etc/passwd", TOKEN, "default", None),
        ("runner-b", "http://runner-b:8002", "short", "default", None),
        ("runner-b", "http://runner-b:8002", TOKEN, "GPU pool", None),
        ("runner-b", "http://runner-b:8002", TOKEN, "default", 0),
    ],
)
def test_add_refuses_invalid_runners(
    name: str, url: str, token: str, pool: str, max_sessions: int | None
) -> None:
    with pytest.raises(RunnerRegistryError):
        add_runner(name, url, token, pool, max_sessions)
    assert all_runners() == []


@pytest.mark.django_db
def test_add_refuses_a_taken_name() -> None:
    add_runner("runner-b", "http://runner-b:8002", TOKEN)

    with pytest.raises(RunnerRegistryError, match="exists"):
        add_runner("runner-b", "http://runner-c:8002", TOKEN)


@pytest.mark.django_db
def test_remove_refuses_the_configured_default(configured: Any) -> None:
    sync_configured_runner()

    with pytest.raises(RunnerRegistryError, match="SECONDSHIFT_RUNNER_SERVICE_URL"):
        remove_runner(DEFAULT_RUNNER)
    assert find_runner(DEFAULT_RUNNER) is not None


@pytest.mark.django_db
def test_remove_and_state_changes_need_an_existing_runner() -> None:
    add_runner("runner-b", "http://runner-b:8002", TOKEN)

    remove_runner("runner-b")

    assert find_runner("runner-b") is None
    with pytest.raises(RunnerRegistryError):
        remove_runner("runner-b")
    with pytest.raises(RunnerRegistryError):
        change_runner_state("runner-b", Runner.State.DRAINING)


@pytest.mark.django_db
def test_runner_client_uses_the_runner_address_and_token() -> None:
    runner = add_runner("runner-b", "http://runner-b:8002", OTHER_TOKEN)

    client = runner_client(runner)

    assert client.base_url == "http://runner-b:8002"
    assert client.session.headers["Authorization"] == f"Bearer {OTHER_TOKEN}"


@pytest.mark.django_db
def test_list_command_never_prints_tokens(
    configured: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    add_runner("gpu-1", "http://gpu-1:8002", OTHER_TOKEN, pool="gpu", max_sessions=2)

    call_command("runners", "list")

    output = capsys.readouterr().out
    assert "default\tactive\tpool=default\tsessions=no limit" in output
    assert "gpu-1\tactive\tpool=gpu\tsessions=2" in output
    assert TOKEN not in output
    assert OTHER_TOKEN not in output


@pytest.mark.django_db
def test_add_command_reads_the_token_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNNER_B_TOKEN", TOKEN)

    call_command(
        "runners",
        "add",
        "runner-b",
        "http://runner-b:8002",
        "--token-env",
        "RUNNER_B_TOKEN",
    )
    call_command("runners", "drain", "runner-b")

    runner = find_runner("runner-b")
    assert runner is not None
    assert runner.token == TOKEN
    assert runner.state == Runner.State.DRAINING


# Routing a run's calls to its runner


class RecordingClient(ClosableFake):
    """Stands in for HttpRunnerClient and records which runner it reached."""

    reached: list[tuple[str, str]] = []

    def __init__(self, base_url: str, auth_token: str) -> None:
        self.base_url = base_url
        self.auth_token = auth_token

    def execute(self, session_id: str, request: CommandRequest) -> CommandResult:
        self.reached.append((self.base_url, "execute"))
        return CommandResult(call_id=request.call_id, exit_code=0, output="")

    def stop(self, session_id: str) -> None:
        self.reached.append((self.base_url, "stop"))


@pytest.fixture
def run(db: None) -> Run:
    return execution_run(db)


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    RecordingClient.reached = []
    monkeypatch.setattr(runners, "HttpRunnerClient", RecordingClient)
    monkeypatch.setattr(
        runners, "get_runner_client", lambda: RecordingClient("configured", "")
    )
    return RecordingClient.reached


def test_a_run_is_pinned_to_the_default_runner(configured: Any, run: Run) -> None:
    runner = assign_runner(run.pk)

    run.refresh_from_db()
    assert runner.name == DEFAULT_RUNNER
    assert run.runner_id == runner.pk


def test_a_pinned_run_keeps_its_runner(configured: Any, run: Run) -> None:
    # As if the session step was retried after a crash, once runner-b was chosen.
    other = add_runner("runner-b", "http://runner-b:8002", OTHER_TOKEN)
    update_run(run.pk, runner=other)

    assert assign_runner(run.pk) == other


def test_a_run_cannot_start_without_a_runner(settings: Any, run: Run) -> None:
    settings.RUNNER_SERVICE_URL = ""

    with pytest.raises(RuntimeError, match="No runner is registered"):
        assign_runner(run.pk)


def test_session_calls_go_to_the_run_runner(
    configured: Any, run: Run, recording: list[tuple[str, str]]
) -> None:
    other = add_runner("runner-b", "http://runner-b:8002", OTHER_TOKEN)
    update_run(run.pk, runner=other)

    execute_runner_command_step.__wrapped__(run.pk, "session", "call", ["ls"], ".", 30)
    stop_runner_session_step.__wrapped__(run.pk, "session")

    assert recording == [
        ("http://runner-b:8002", "execute"),
        ("http://runner-b:8002", "stop"),
    ]


def test_a_run_from_before_the_registry_uses_the_configured_runner(
    configured: Any, run: Run, recording: list[tuple[str, str]]
) -> None:
    stop_runner_session_step.__wrapped__(run.pk, "session")

    assert recording == [("configured", "stop")]


def test_a_runner_with_unfinished_runs_cannot_be_removed(
    configured: Any, run: Run
) -> None:
    other = add_runner("runner-b", "http://runner-b:8002", OTHER_TOKEN)
    update_run(run.pk, runner=other)

    with pytest.raises(RunnerRegistryError, match="unfinished runs"):
        remove_runner("runner-b")

    Run.objects.filter(pk=run.pk).update(status=Run.Status.SUCCEEDED)
    remove_runner("runner-b")
    run.refresh_from_db()
    assert run.runner_id is None
