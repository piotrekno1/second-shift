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
# How long the agent's step waits for the relay to finish its last call.
STOP_WAIT_SECONDS = COLLECT_WAIT_SECONDS + 20


def relayed_answer(lease: AgentLease, call: RelayedCall) -> RelayedAnswer:
    status, payload = broker_answer(lease, call.body)
    body = b"" if payload is None else json.dumps(payload).encode()
    return RelayedAnswer(status, body)


def relay_calls(
    client: HttpRunnerClient,
    session_id: str,
    lease: AgentLease,
    stop: threading.Event,
) -> None:
    """Answer the session's relayed calls until `stop` is set, or the runner
    says it doesn't relay.
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
            answer = relayed_answer(lease, call)
            try:
                client.answer_agent_call(session_id, call.id, answer)
            except RunnerProtocolError as error:
                # The agent's call times out and the agent sees an error.
                logger.warning("could not answer agent call %s: %s", call.id, error)


def relay_thread(
    client_for: Callable[[], HttpRunnerClient],
    session_id: str,
    lease: AgentLease,
    stop: threading.Event,
) -> None:
    try:
        with client_for() as client:
            relay_calls(client, session_id, lease, stop)
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
