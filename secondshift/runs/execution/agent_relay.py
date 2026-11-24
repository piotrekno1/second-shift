"""Answer the tool calls a runner relays for its agent.

A runner on another host can't reach the gateway's agent broker, so its agent
sends tool calls to the runner. While the agent runs, a thread here collects
them over the worker's own connection to the runner and answers each with the
same checks the broker uses. Nothing calls into the control plane.
"""

import json
import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from django.db import connections

from secondshift.runner_protocol.client import HttpRunnerClient
from secondshift.runner_protocol.relay import (
    COLLECT_WAIT_SECONDS,
    RelayedAnswer,
    RelayedCall,
)
from secondshift.runner_protocol.sessions import RunnerProtocolError
from secondshift.runs.execution.agent_broker import broker_answer
from secondshift.runs.models import AgentLease

logger = logging.getLogger(__name__)

# How long to wait before asking a runner again after a failed collection.
RETRY_SECONDS = 2
# Calls of one agent answered at once. Claude Code may call several tools at
# once, and the gateway answers those in parallel too. The runner lets an
# agent have 16 calls waiting.
PARALLEL_CALLS = 4
# How long the agent's step waits for the relay to finish its last call.
STOP_WAIT_SECONDS = COLLECT_WAIT_SECONDS + 20


def relayed_answer(lease: AgentLease, call: RelayedCall) -> RelayedAnswer:
    status, payload = broker_answer(lease, call.body)
    body = b"" if payload is None else json.dumps(payload).encode()
    return RelayedAnswer(status, body)


Submit = Callable[[Callable[[], None]], object]


def answer_call(
    client: HttpRunnerClient, session_id: str, lease: AgentLease, call: RelayedCall
) -> None:
    try:
        answer = relayed_answer(lease, call)
        client.answer_agent_call(session_id, call.id, answer)
    except RunnerProtocolError as error:
        # The agent's call times out and the agent sees an error.
        logger.warning("could not answer agent call %s: %s", call.id, error)
    except Exception:
        logger.exception("answering agent call %s failed", call.id)


def relay_calls(
    client: HttpRunnerClient,
    session_id: str,
    lease: AgentLease,
    stop: threading.Event,
    submit: Submit,
) -> None:
    """Answer the session's relayed calls until `stop` is set, or the runner
    says it doesn't relay. `submit` runs each answer, in parallel with others.
    """
    while not stop.is_set():
        try:
            calls = client.agent_calls(session_id)
        except RunnerProtocolError as error:
            logger.warning(
                "could not collect agent calls for %s: %s", session_id, error
            )
            stop.wait(RETRY_SECONDS)
            continue
        if calls is None:
            return
        for call in calls:
            submit(lambda call=call: answer_call(client, session_id, lease, call))


def with_own_connections(work: Callable[[], None]) -> Callable[[], None]:
    """Close the database connections the work opened in its pool thread."""

    def run() -> None:
        try:
            work()
        finally:
            connections.close_all()

    return run


def relay_thread(
    client_for: Callable[[], HttpRunnerClient],
    session_id: str,
    lease: AgentLease,
    stop: threading.Event,
) -> None:
    try:
        with (
            client_for() as client,
            ThreadPoolExecutor(
                PARALLEL_CALLS, thread_name_prefix=f"agent-call-{session_id}"
            ) as pool,
        ):
            relay_calls(
                client,
                session_id,
                lease,
                stop,
                lambda work: pool.submit(with_own_connections(work)),
            )
    except Exception:
        logger.exception("relaying agent calls for %s failed", session_id)
    finally:
        # This thread's own database connections.
        connections.close_all()


@contextmanager
def relaying(
    client_for: Callable[[], HttpRunnerClient], session_id: str, lease: AgentLease
) -> Iterator[None]:
    """Answer relayed calls while the block runs the agent."""
    stop = threading.Event()
    thread = threading.Thread(
        target=relay_thread,
        args=(client_for, session_id, lease, stop),
        name=f"agent-relay-{session_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(STOP_WAIT_SECONDS)
