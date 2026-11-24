"""Tool calls from this runner's agents, kept until the worker answers them.

See secondshift.runner_protocol.relay. The runner checks the agent's broker
token and passes each call and its answer through without reading them.
"""

import hashlib
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest

from secondshift.runner import dao
from secondshift.runner_protocol.relay import RelayedAnswer, RelayedCall

# How long an agent waits for the worker to answer one call.
ANSWER_WAIT_SECONDS = 600
# How often a waiting request looks for news.
POLL_SECONDS = 0.2
# Calls one agent may have waiting at once.
MAX_UNANSWERED_CALLS = 16


class RelayFull(Exception):
    pass


def relay_enabled() -> bool:
    return bool(settings.AGENT_RELAY_PORT)


def on_relay_port(request: HttpRequest) -> bool:
    """Whether the request came in on the relay port, which agents reach.

    The port is the listening socket's, not anything the caller sends.
    """
    return relay_enabled() and request.META.get("SERVER_PORT") == str(
        settings.AGENT_RELAY_PORT
    )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@contextmanager
def relaying(session_id: str, broker_token: str) -> Iterator[None]:
    """Accept the agent's calls while it runs. Nothing when the relay is off."""
    if not relay_enabled():
        yield
        return
    dao.open_relay(session_id, token_hash(broker_token))
    try:
        yield
    finally:
        dao.close_relay(session_id)


def submit_call(broker_token: str, body: bytes) -> UUID | None:
    """Keep a call for the worker. None when no running agent has this token."""
    relay = dao.relay_for_token(token_hash(broker_token)) if broker_token else None
    if relay is None:
        return None
    if dao.unanswered_call_count(relay.pk) >= MAX_UNANSWERED_CALLS:
        raise RelayFull
    return dao.add_call(relay.pk, body).pk


def wait_for_answer(
    call_id: UUID,
    timeout: float = ANSWER_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> RelayedAnswer | None:
    """The worker's answer, or None when it doesn't come in time or the agent
    ended. The call is gone either way.
    """
    deadline = clock() + timeout
    while True:
        call = dao.find_call(call_id)
        if call is None:
            return None
        if call.answer_status is not None:
            dao.delete_call(call_id)
            return RelayedAnswer(call.answer_status, bytes(call.answer or b""))
        if clock() >= deadline:
            dao.delete_call(call_id)
            return None
        sleep(POLL_SECONDS)


def collect_calls(
    session_id: str,
    wait: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[RelayedCall]:
    """The session's new calls, waiting up to `wait` seconds for one."""
    deadline = clock() + wait
    while True:
        calls = dao.take_calls(session_id)
        if calls or clock() >= deadline:
            return [RelayedCall(str(call.pk), bytes(call.body)) for call in calls]
        sleep(POLL_SECONDS)


def answer_call(session_id: str, call_id: UUID, answer: RelayedAnswer) -> bool:
    return dao.set_call_answer(session_id, call_id, answer.status, answer.body)
