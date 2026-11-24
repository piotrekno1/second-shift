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

import hmac
import logging
import math
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from secondshift.connections.dao import known_credential_keys
from secondshift.runner_protocol.client import HttpRunnerClient, get_runner_client
from secondshift.runner_protocol.credentials import key_fingerprint
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
CAPACITY_WAIT = "Waiting for a runner with room."
CAPACITY_TIMEOUT = "No runner had room before the run's time limit."
LOGIN_WAIT = "Waiting for another run using this login to finish."
LOGIN_TIMEOUT = (
    "Another run used this login until the run's time limit. A login runs one "
    "job at a time."
)
# After its checks have failed for this long, a runner takes no new runs, and
# logins whose home it is move. The worker checks every minute.
UNREACHABLE_SECONDS = 2 * 60


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


def ensure_configured_runner() -> None:
    """Register the configured runner if the worker has not yet.

    The worker registers it on its first pass. A run or a login may come
    first on a new install, or after an operator added another runner.
    """
    if dao.find_runner(DEFAULT_RUNNER) is None:
        sync_configured_runner()


def token_in_use(token: str) -> bool:
    """Whether another runner, or the configured one, has this token.

    Each runner gets its own, so one that is taken over can't call the others.
    """
    tokens = [runner.token for runner in dao.all_runners()]
    if address := configured_runner_address():
        tokens.append(address[1])
    return any(hmac.compare_digest(token.encode(), other.encode()) for other in tokens)


def check_runner_fields(
    name: str, url: str, token: str, pool: str, allow_http: bool = False
) -> None:
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
        raise RunnerRegistryError("A runner URL starts with https://.")
    # Its token, repository snapshots and command output cross the network.
    if parts.scheme == "http" and not allow_http:
        raise RunnerRegistryError(
            "A runner URL uses https://. Plain http:// sends the runner's token, "
            "repository files and command output unencrypted. Allow it only on "
            "a private network you trust."
        )
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
    allow_http: bool = False,
) -> Runner:
    check_runner_fields(name, url, token, pool, allow_http)
    if max_sessions is not None and max_sessions < 1:
        raise RunnerRegistryError("A runner's session limit is at least 1.")
    if token_in_use(token):
        raise RunnerRegistryError(
            "Another runner has this token. Give each runner its own."
        )
    try:
        return dao.create_runner(name, url.rstrip("/"), token, pool, max_sessions)
    except IntegrityError:
        raise RunnerRegistryError(f"A runner named {name} exists.") from None


def change_runner_state(name: str, state: Runner.State) -> None:
    if not dao.set_runner_state(name, state):
        raise RunnerRegistryError(f"There is no runner named {name}.")


def remove_logins_from(runner: Runner) -> bool:
    """Ask the runner to delete every login copy it could hold.

    True once it confirmed each one. Copies live only on the runner, so this
    is the last chance to remove them through SecondShift.
    """
    try:
        with runner_client(runner) as client:
            for key in sorted(known_credential_keys()):
                response = client.send("DELETE", f"/v1/agent-credentials/{key.hex}")
                if response.status_code != 204:
                    return False
    except RunnerProtocolError:
        return False
    return True


def remove_runner(name: str, force: bool = False) -> bool:
    """Forget a runner. True when it confirmed it deleted its login copies.

    A runner that does not confirm is kept, unless forced. Then its
    agent-auth directory has to be deleted on the host by hand.
    """
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
    removed = remove_logins_from(runner)
    if not removed and not force:
        raise RunnerRegistryError(
            f"Runner {name} did not confirm it deleted its cached agent logins. "
            "Make it reachable and try again, or force the removal and delete "
            "its agent-auth directory yourself."
        )
    if not dao.delete_runner(name):
        raise RunnerRegistryError(f"There is no runner named {name}.")
    return removed


def runner_client(runner: Runner) -> HttpRunnerClient:
    """A client for this runner, with its own token and pinned key."""
    return HttpRunnerClient(
        base_url=runner.url,
        auth_token=runner.token,
        provisioning_key=runner.provisioning_key,
        first_key=lambda key: pin_first_key(runner, key),
    )


def pin_first_key(runner: Runner, key: str) -> str:
    """Pin this key if the runner has none yet, and return the one pinned.

    The worker's refresh usually pins it first. Whichever comes first wins,
    and the other is checked against it.
    """
    if dao.pin_provisioning_key(runner.pk, key):
        logger.info(
            "pinned runner %s provisioning key %s", runner.name, key_fingerprint(key)
        )
    return dao.pinned_provisioning_key(runner.pk)


def fingerprint(runner: Runner) -> str:
    """Its pinned key's fingerprint, or why there is none."""
    if not runner.provisioning_key:
        return "not pinned yet"
    return key_fingerprint(runner.provisioning_key)


def trust_current_key(name: str) -> tuple[str, str]:
    """Pin the key the runner answers with now. Returns the old and new
    fingerprints.

    For a runner whose key changed on purpose, such as after its agent-auth
    directory was replaced.
    """
    runner = dao.find_runner(name)
    if runner is None:
        raise RunnerRegistryError(f"There is no runner named {name}.")
    try:
        with runner_client(runner) as client:
            key = client.current_provisioning_key()
    except RunnerProtocolError as error:
        raise RunnerRegistryError(f"Runner {name} did not answer: {error}") from None
    old = fingerprint(runner)
    dao.replace_provisioning_key(runner.pk, key)
    return old, key_fingerprint(key)


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

    Waiting helps a runner that is full, draining or not answering, but not a
    missing one.
    """
    ensure_configured_runner()
    if not dao.all_runners():
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


def login_candidates(
    active: list[Runner], credential_key: str | None, environment: str
) -> list[Runner]:
    """The runners a run may go to, given the subscription login it uses.

    A login's runs go to its home runner while that runner is active,
    answering, and offers the environment. Otherwise the login moves, and the runner chosen
    becomes its home. Codex may then need a reconnect, since the old home kept
    the tokens it last refreshed.
    """
    if not credential_key:
        return active
    home = dao.login_home(credential_key)
    if home is None:
        return active
    homes = [runner for runner in active if runner.pk == home.runner_id]
    if homes and offers(homes[0], environment):
        return homes
    return active


def try_assign_runner(
    run_id: int, environment: str, credential_key: str | None = None
) -> Runner | None:
    """Pin the run to a runner with room. None when every one is full, or
    when another run is using the run's subscription login.

    A runner lets one run use a login at a time and fails any other, so the
    run waits here instead. The run row and the candidate runners stay locked
    while choosing, so two workers never both take a runner's last session or
    a login, and a step that is retried after a crash finds the runner it
    saved.
    """
    with transaction.atomic():
        # PostgreSQL can't lock the nullable side of a join, so the runner
        # is loaded on its own.
        run = dao.lock_run(run_id)
        if run.runner_id is not None:
            return run.runner
        runners = dao.lock_runners_in(
            DEFAULT_POOL, Runner.State.ACTIVE, UNREACHABLE_SECONDS
        )
        if credential_key and dao.login_in_use(credential_key, run_id):
            return None
        candidates = login_candidates(runners, credential_key, environment)
        counts = dao.unfinished_run_counts([runner.pk for runner in candidates])
        runner = choose_runner(candidates, counts, environment)
        if runner is not None:
            dao.update_run(run_id, runner=runner)
            if credential_key:
                dao.set_login_home(credential_key, runner)
        return runner


def assign_runner(run_id: int, sleep: Callable[[float], None] = time.sleep) -> Runner:
    """The runner this run is pinned to, choosing one the first time.

    While every runner that could take it is full, draining or not answering,
    or another run is using its subscription login, it waits, up to the run's
    time limit. It fails at once when no runner could ever take it.
    """
    run = dao.get_run(run_id, "runner")
    # A retried step keeps its runner, whatever has changed since.
    if run.runner is not None:
        return run.runner
    environment = run.plan.runner.environment
    credential_key = run.plan.execution.credential_key
    deadline = run_deadline(run)
    while True:
        if problem := placement_problem(environment):
            raise RuntimeError(problem)
        if runner := try_assign_runner(run_id, environment, credential_key):
            return runner
        login_busy = (
            dao.login_in_use(credential_key, run_id) if credential_key else False
        )
        if timezone.now() >= deadline:
            raise RuntimeError(LOGIN_TIMEOUT if login_busy else CAPACITY_TIMEOUT)
        dao.add_event_once(run_id, LOGIN_WAIT if login_busy else CAPACITY_WAIT, "info")
        sleep(CAPACITY_POLL_SECONDS)


def runner_client_for(run_id: int) -> HttpRunnerClient:
    """A client for the runner this run is pinned to.

    A run that started before runners were registered has none, and keeps
    using the configured runner.
    """
    runner = dao.get_run(run_id, "runner").runner
    if runner is None:
        return get_runner_client()
    return runner_client(runner)


def registered_runners() -> list[Runner]:
    """Every registered runner, the configured one included."""
    ensure_configured_runner()
    return dao.all_runners()


def runner_for_login(credential_key: str) -> Runner | None:
    """The runner to check a login on: its home, so the check sees the copy
    its runs use.

    A login with no active home gets the active default-pool runner with the
    most free sessions, which becomes its home. None when every one is full,
    draining, down or not answering.
    """
    ensure_configured_runner()
    with transaction.atomic():
        runners = dao.lock_runners_in(
            DEFAULT_POOL, Runner.State.ACTIVE, UNREACHABLE_SECONDS
        )
        home = dao.login_home(credential_key)
        for runner in runners:
            # A check is short, so the home takes it even when full.
            if home is not None and runner.pk == home.runner_id:
                return runner
        counts = dao.unfinished_run_counts([runner.pk for runner in runners])
        chosen = choose_runner(runners, counts, DEFAULT_ENVIRONMENT)
        if chosen is not None:
            dao.set_login_home(credential_key, chosen)
        return chosen
