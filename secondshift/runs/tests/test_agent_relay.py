"""The worker answers the tool calls a runner relays for its agent."""

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Self

import pytest
from django.contrib.auth.models import User

from secondshift.runner_protocol.relay import RelayedAnswer, RelayedCall
from secondshift.runner_protocol.schemas import AgentInput, AgentOutput
from secondshift.runner_protocol.sessions import RunnerUnreachable
from secondshift.runs.dao import create_lease, end_lease
from secondshift.runs.execution import agent_relay, cli_agent
from secondshift.runs.execution.agent_broker import broker_answer
from secondshift.runs.execution.agent_relay import relay_calls, relaying
from secondshift.runs.execution.cli_agent import execute_cli_step
from secondshift.runs.models import AgentLease, Run

LIST_TOOLS = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}).encode()
INITIALIZED = json.dumps(
    {"jsonrpc": "2.0", "method": "notifications/initialized"}
).encode()


@pytest.fixture(autouse=True)
def auth_root(settings: Any, tmp_path: Path) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"


def lease_for(run: Run) -> AgentLease:
    return create_lease(run, hashlib.sha256(b"token").hexdigest(), "run-1")


class RelayingRunner:
    """A runner that relays: it hands out `batches` of calls in turn, then
    says it has none until stopped.
    """

    def __init__(
        self, batches: list[list[RelayedCall] | Exception], stop: threading.Event
    ) -> None:
        self.batches = batches
        self.stop = stop
        self.answers: dict[str, RelayedAnswer] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def agent_calls(self, session_id: str) -> list[RelayedCall] | None:
        if not self.batches:
            self.stop.set()
            return []
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch

    def answer_agent_call(
        self, session_id: str, call_id: str, answer: RelayedAnswer
    ) -> None:
        self.answers[call_id] = answer


@pytest.mark.django_db
def test_relayed_calls_get_the_broker_s_answers(
    agent_run: tuple[Run, User],
) -> None:
    run, _ = agent_run
    lease = lease_for(run)
    stop = threading.Event()
    runner = RelayingRunner(
        [[RelayedCall("a", LIST_TOOLS), RelayedCall("b", INITIALIZED)]], stop
    )

    relay_calls(runner, "run-1", lease, stop)  # type: ignore[arg-type]

    status, payload = broker_answer(lease, LIST_TOOLS)
    assert runner.answers["a"] == RelayedAnswer(status, json.dumps(payload).encode())
    assert json.loads(runner.answers["a"].body)["result"]["tools"]
    # A notification is accepted with no body, as the broker answers it.
    assert runner.answers["b"] == RelayedAnswer(202, b"")


@pytest.mark.django_db
def test_a_relayed_call_after_the_agent_ended_is_refused(
    agent_run: tuple[Run, User],
) -> None:
    run, _ = agent_run
    lease = lease_for(run)
    end_lease(lease.pk)
    lease.refresh_from_db()
    stop = threading.Event()
    runner = RelayingRunner([[RelayedCall("a", LIST_TOOLS)]], stop)

    relay_calls(runner, "run-1", lease, stop)  # type: ignore[arg-type]

    error = json.loads(runner.answers["a"].body)["error"]
    assert error["message"] == "Agent access denied or tool request invalid."


@pytest.mark.django_db
def test_collection_goes_on_after_the_runner_did_not_answer(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    stop = threading.Event()
    monkeypatch.setattr(agent_relay, "RETRY_SECONDS", 0)
    runner = RelayingRunner(
        [
            RunnerUnreachable("The runner did not answer in time."),
            [RelayedCall("a", LIST_TOOLS)],
        ],
        stop,
    )

    relay_calls(runner, "run-1", lease_for(run), stop)  # type: ignore[arg-type]

    assert "a" in runner.answers


def test_collection_ends_when_the_runner_does_not_relay() -> None:
    class LocalRunner(RelayingRunner):
        def agent_calls(self, session_id: str) -> list[RelayedCall] | None:
            return None

    stop = threading.Event()

    relay_calls(LocalRunner([], stop), "run-1", None, stop)  # type: ignore[arg-type]

    assert not stop.is_set()


def test_the_relay_thread_stops_with_the_agent() -> None:
    stop_seen = threading.Event()

    class QuietRunner(RelayingRunner):
        def agent_calls(self, session_id: str) -> list[RelayedCall] | None:
            stop_seen.wait(0.05)
            return []

    runner = QuietRunner([], threading.Event())
    with relaying(lambda: runner, "run-1", None):  # type: ignore[arg-type,return-value]
        threads = [t for t in threading.enumerate() if t.name == "agent-relay-run-1"]
        assert threads

    assert not threads[0].is_alive()


@pytest.mark.django_db(transaction=True)
def test_an_agent_s_tool_call_is_answered_while_it_runs(
    agent_run: tuple[Run, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = agent_run
    answered = threading.Event()
    answers: dict[str, RelayedAnswer] = {}

    class RemoteRunner:
        """A runner on another host: the agent's call arrives through it."""

        calls = [[RelayedCall("a", LIST_TOOLS)]]

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def encrypted_auth(self, auth: str) -> str:
            return "sealed"

        def agent_calls(self, session_id: str) -> list[RelayedCall] | None:
            return self.calls.pop(0) if self.calls else []

        def answer_agent_call(
            self, session_id: str, call_id: str, answer: RelayedAnswer
        ) -> None:
            answers[call_id] = answer
            answered.set()

        def run_agent(self, session_id: str, request: AgentInput) -> AgentOutput:
            # The agent waits for its tool call's answer before it finishes.
            assert answered.wait(10)
            return AgentOutput(report="Done", succeeded=True, reason="completed")

    monkeypatch.setattr(cli_agent, "runner_client_for", lambda run_id: RemoteRunner())

    result = execute_cli_step(run.pk, "run-1", "Read the skill")

    assert result["succeeded"] is True
    assert json.loads(answers["a"].body)["result"]["tools"]
