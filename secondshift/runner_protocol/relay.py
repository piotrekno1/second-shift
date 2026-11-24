"""Agent tool calls relayed through the runner.

A runner on another host can't reach the gateway's agent broker. Instead its
agents send tool calls to the runner, which keeps them until the worker that
runs the agent collects them over its own connection to the runner, answers
them, and sends the answers back. The runner never reads the calls.
"""

from dataclasses import dataclass

# What the agent broker accepts, and what the relay accepts in its place.
MAX_CALL_BYTES = 1024 * 1024
# Tool results are capped by the run's limits. This bounds what a runner
# stores for one answer.
MAX_ANSWER_BYTES = 8 * 1024 * 1024
# How long the runner holds the worker's collection open while no call waits.
COLLECT_WAIT_SECONDS = 5
# What a runner that doesn't relay answers, so the worker stops asking.
RELAY_OFF = "This runner does not relay agent calls."


@dataclass(frozen=True)
class RelayedCall:
    """One tool call an agent sent, as the runner keeps it."""

    id: str
    body: bytes


@dataclass(frozen=True)
class RelayedAnswer:
    """The broker's HTTP answer to a call, passed back to the agent as is."""

    status: int
    body: bytes
