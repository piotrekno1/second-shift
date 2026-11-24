"""The runners the control plane can place runs on.

The operator registers runners with `secondshift runners`. The runner named by
SECONDSHIFT_RUNNER_SERVICE_URL and SECONDSHIFT_RUNNER_SERVICE_TOKEN is kept as
`default`, so an install with one runner needs no registration.

A run is pinned to one runner when its session starts, and every later call for
the run goes there. Placement picks an active runner in the default pool that
offers the run's environment and has room, preferring the one with the most
free sessions. Runners in other pools take no runs until pools are routed. See
docs/proposals/multiple-runners.md for the rollout.
"""

import logging
import math
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from secondshift.runner_protocol.client import HttpRunnerClient, get_runner_client
from secondshift.runner_protocol.environments import DEFAULT_ENVIRONMENT
from secondshift.runner_protocol.sessions import RunnerProtocolError
from secondshift.runs import dao
from secondshift.runs.execution.deadlines import run_deadline
from secondshift.runs.models import Runner

logger = logging.getLogger(__name__)

DEFAULT_RUNNER = "default"
DEFAULT_POOL = "default"
# The same minimum production settings require of the configured token.
MIN_TOKEN_LENGTH = 32
# How long a run waits between looks for a runner with room.
CAPACITY_POLL_SECONDS = 5
# A runner that hangs must not hold up the worker's other tasks.
ENVIRONMENTS_TIMEOUT_SECONDS = 3


class RunnerRegistryError(ValueError):
    """A runner change the registry refused. The message is for the operator."""


def configured_runner_address() -> tuple[str, str] | None:
    """The URL and token in settings, or None when either is missing."""
    url = settings.RUNNER_SERVICE_URL.strip()
    token = settings.RUNNER_SERVICE_TOKEN
    if not url or not token:
        return None
    return url, token


def sync_configured_runner() -> Runner | None:
    """Keep the configured runner registered as `default`.

    Settings own its URL and token. The operator owns its state, pool and
    capacity, so a drain survives restarts.
    """
    address = configured_runner_address()
    if address is None:
        return None
    url, token = address
    return dao.upsert_runner_address(DEFAULT_RUNNER, url, token)


def check_runner_fields(name: str, url: str, token: str, pool: str) -> None:
    if not name or slugify(name) != name:
        raise RunnerRegistryError(
            "A runner name uses lowercase letters, digits and hyphens."
        )
    if not pool or slugify(pool) != pool:
        raise RunnerRegistryError(
            "A pool name uses lowercase letters, digits and hyphens."
        )
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RunnerRegistryError("A runner URL starts with http:// or https://.")
    if len(token) < MIN_TOKEN_LENGTH:
        raise RunnerRegistryError(
            f"A runner token has at least {MIN_TOKEN_LENGTH} characters."
        )


def add_runner(
    name: str,
    url: str,
    token: str,
    pool: str = DEFAULT_POOL,
    max_sessions: int | None = None,
) -> Runner:
    check_runner_fields(name, url, token, pool)
    if max_sessions is not None and max_sessions < 1:
        raise RunnerRegistryError("A runner's session limit is at least 1.")
    try:
        return dao.create_runner(name, url.rstrip("/"), token, pool, max_sessions)
    except IntegrityError:
        raise RunnerRegistryError(f"A runner named {name} exists.") from None


def change_runner_state(name: str, state: Runner.State) -> None:
    if not dao.set_runner_state(name, state):
        raise RunnerRegistryError(f"There is no runner named {name}.")


def remove_runner(name: str) -> None:
    if name == DEFAULT_RUNNER and configured_runner_address() is not None:
        raise RunnerRegistryError(
            "The default runner comes from SECONDSHIFT_RUNNER_SERVICE_URL. "
            "Unset it first, or drain the runner instead."
        )
    runner = dao.find_runner(name)
    if runner is None:
        raise RunnerRegistryError(f"There is no runner named {name}.")
    if dao.runner_has_unfinished_runs(runner.pk):
        raise RunnerRegistryError(
            f"Runner {name} has unfinished runs. Drain it and wait for them."
        )
    if not dao.delete_runner(name):
        raise RunnerRegistryError(f"There is no runner named {name}.")


def runner_client(runner: Runner) -> HttpRunnerClient:
    """A client for this runner, with its own token."""
    return HttpRunnerClient(base_url=runner.url, auth_token=runner.token)


def offers(runner: Runner, environment: str) -> bool:
    """Whether the runner offers this environment. Unknown counts as yes, so a
    runner that has not answered yet is not left out; it refuses a session
    for an environment it lacks."""
    return (
        environment == DEFAULT_ENVIRONMENT
        or runner.environments is None
        or environment in runner.environments
    )


def free_sessions(runner: Runner, unfinished: int) -> float:
    if runner.max_sessions is None:
        return math.inf
    return runner.max_sessions - unfinished


def choose_runner(
    runners: list[Runner], counts: dict[int, int], environment: str
) -> Runner | None:
    """The runner with the most free sessions that offers the environment.

    Ties go to the oldest runner. None when every such runner is full.
    """
    fitting = [
        runner
        for runner in runners
        if offers(runner, environment)
        and free_sessions(runner, counts.get(runner.pk, 0)) > 0
    ]
    if not fitting:
        return None
    return min(
        fitting,
        key=lambda runner: (
            -free_sessions(runner, counts.get(runner.pk, 0)),
            runner.pk,
        ),
    )


def placement_problem(environment: str) -> str:
    """Why no runner could ever take this run, or nothing when one could.

    Waiting helps a full or draining runner, but not a missing one.
    """
    pool = dao.runners_in_pool(DEFAULT_POOL)
    if not pool:
        # A new install may start a run before the worker registers its runner.
        if sync_configured_runner() is None:
            return (
                "No runner is registered. Set SECONDSHIFT_RUNNER_SERVICE_URL "
                "or add one with secondshift runners add."
            )
        pool = dao.runners_in_pool(DEFAULT_POOL)
    if not pool:
        return "No runner is in the default pool."
    if not any(offers(runner, environment) for runner in pool):
        return f"No runner offers the {environment} environment."
    return ""


def try_assign_runner(run_id: int, environment: str) -> Runner | None:
    """Pin the run to a runner with room. None when every one is full.

    The run row and the candidate runners stay locked while choosing, so two
    workers never both take a runner's last session, and a step that is
    retried after a crash finds the runner it saved.
    """
    with transaction.atomic():
        # PostgreSQL can't lock the nullable side of a join, so the runner
        # is loaded on its own.
        run = dao.lock_run(run_id)
        if run.runner_id is not None:
            return run.runner
        runners = dao.lock_runners_in(DEFAULT_POOL, Runner.State.ACTIVE)
        counts = dao.unfinished_run_counts([runner.pk for runner in runners])
        runner = choose_runner(runners, counts, environment)
        if runner is not None:
            dao.update_run(run_id, runner=runner)
        return runner


def assign_runner(run_id: int, sleep: Callable[[float], None] = time.sleep) -> Runner:
    """The runner this run is pinned to, choosing one the first time.

    While every runner that could take it is full or draining, it waits, up to
    the run's time limit. It fails at once when no runner could ever take it.
    """
    run = dao.get_run(run_id, "runner")
    # A retried step keeps its runner, whatever has changed since.
    if run.runner is not None:
        return run.runner
    environment = run.plan.runner.environment
    deadline = run_deadline(run)
    while True:
        if problem := placement_problem(environment):
            raise RuntimeError(problem)
        if runner := try_assign_runner(run_id, environment):
            return runner
        if timezone.now() >= deadline:
            raise RuntimeError("No runner had room before the run's time limit.")
        dao.add_event_once(run_id, "Waiting for a runner with room.", "info")
        sleep(CAPACITY_POLL_SECONDS)


def refresh_runner_environments() -> int:
    """Ask each runner which environments it offers. Returns how many answered.

    A runner that does not answer keeps what it said last.
    """
    answered = 0
    for runner in dao.all_runners():
        if runner.state == Runner.State.DOWN:
            continue
        try:
            with runner_client(runner) as client:
                offered = client.environments(timeout=ENVIRONMENTS_TIMEOUT_SECONDS)
        except RunnerProtocolError:
            logger.warning("runner %s did not list its environments", runner.name)
            continue
        dao.set_runner_environments(runner.pk, sorted(offered.images))
        answered += 1
    return answered


def runner_client_for(run_id: int) -> HttpRunnerClient:
    """A client for the runner this run is pinned to.

    A run that started before runners were registered has none, and keeps
    using the configured runner.
    """
    runner = dao.get_run(run_id, "runner").runner
    if runner is None:
        return get_runner_client()
    return runner_client(runner)
