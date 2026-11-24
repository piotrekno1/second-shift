"""The runners the control plane can place runs on.

The operator registers runners with `secondshift runners`. The runner named by
SECONDSHIFT_RUNNER_SERVICE_URL and SECONDSHIFT_RUNNER_SERVICE_TOKEN is kept as
`default`, so an install with one runner needs no registration.

A run is pinned to one runner when its session starts, and every later call for
the run goes there. For now every run is pinned to `default`. See
docs/proposals/multiple-runners.md for the rollout.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.text import slugify

from secondshift.runner_protocol.client import HttpRunnerClient, get_runner_client
from secondshift.runs import dao
from secondshift.runs.models import Runner

DEFAULT_RUNNER = "default"
DEFAULT_POOL = "default"
# The same minimum production settings require of the configured token.
MIN_TOKEN_LENGTH = 32


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


def assign_runner(run_id: int) -> Runner:
    """The runner this run is pinned to, choosing one the first time.

    The run row stays locked while choosing, so a step that is retried after a
    crash finds the runner it saved and opens its session there again.
    """
    with transaction.atomic():
        # PostgreSQL can't lock the nullable side of a join, so the runner
        # is loaded on its own.
        run = dao.lock_run(run_id)
        if run.runner_id is not None:
            return run.runner
        runner = dao.find_runner(DEFAULT_RUNNER) or sync_configured_runner()
        if runner is None:
            raise RuntimeError(
                "No runner is registered. Set SECONDSHIFT_RUNNER_SERVICE_URL "
                "or add one with secondshift runners add."
            )
        dao.update_run(run_id, runner=runner)
        return runner


def runner_client_for(run_id: int) -> HttpRunnerClient:
    """A client for the runner this run is pinned to.

    A run that started before runners were registered has none, and keeps
    using the configured runner.
    """
    runner = dao.get_run(run_id, "runner").runner
    if runner is None:
        return get_runner_client()
    return runner_client(runner)
