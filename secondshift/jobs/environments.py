"""The sandbox environments the runner offers.

Only the runner maps an environment's name to an image. The control plane
asks it which names exist, and a run sends just the name.
"""

import time
from dataclasses import dataclass

from secondshift.runner_protocol.client import get_runner_client
from secondshift.runner_protocol.sessions import RunnerProtocolError

# A runner's environments change only when it restarts. Remembering its answer
# for a minute saves a call on every page, and a restart still shows soon.
REMEMBER_SECONDS = 60
# Pages wait on this call, so a runner that hangs must not hold them up.
ASK_TIMEOUT_SECONDS = 3


@dataclass
class Answer:
    asked_at: float
    environments: dict[str, str] | None


last_answer = Answer(asked_at=float("-inf"), environments=None)


def fetch_environments() -> dict[str, str]:
    return get_runner_client().environments(timeout=ASK_TIMEOUT_SECONDS)


def runner_environments() -> dict[str, str] | None:
    """Each environment and its image, by name, or None while the runner
    can't be reached.
    """
    global last_answer
    now = time.monotonic()
    if now - last_answer.asked_at < REMEMBER_SECONDS:
        return last_answer.environments
    try:
        environments = fetch_environments()
    except RunnerProtocolError:
        environments = None
    last_answer = Answer(asked_at=now, environments=environments)
    return environments


def forget_runner_environments() -> None:
    """Ask the runner again next time."""
    global last_answer
    last_answer = Answer(asked_at=float("-inf"), environments=None)


def environment_missing(name: str) -> bool:
    """True when the runner answered and has no such environment. While it
    can't be reached, a run may still start, and the runner then refuses it.
    """
    environments = runner_environments()
    return environments is not None and name not in environments
