"""Placing a run on a runner: environment, capacity, pools and waiting."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from secondshift.runner_protocol.environments import RunnerEnvironments
from secondshift.runner_protocol.sessions import RunnerProtocolError
from secondshift.runner_protocol.tests.keys import public_key_pem
from secondshift.runs import runners
from secondshift.runs.dao import (
    find_runner,
    login_home,
    run_events,
    set_login_home,
    update_run,
)
from secondshift.runs.models import LoginHome, Run, Runner
from secondshift.runs.runners import (
    add_runner,
    assign_runner,
    change_runner_state,
    choose_runner,
    refresh_runners,
    sync_configured_runner,
    try_assign_runner,
)
from secondshift.runs.tests.test_workflows import execution_run

TOKEN = "t" * 32
# execution_run's plan asks for the node environment.
NODE = "node"


@pytest.fixture
def run(db: None, settings: Any) -> Run:
    settings.RUNNER_SERVICE_URL = "http://runner-a:8002"
    settings.RUNNER_SERVICE_TOKEN = TOKEN
    return execution_run(db)


def token_for(name: str) -> str:
    """Each runner has its own token."""
    return f"{name}-token".ljust(32, "x")


def make_runner(
    name: str,
    max_sessions: int | None = None,
    environments: list[str] | None = None,
    pool: str = "default",
) -> Runner:
    runner = add_runner(
        name,
        f"https://{name}:8002",
        token_for(name),
        pool=pool,
        max_sessions=max_sessions,
    )
    runner.environments = environments
    runner.save(update_fields=["environments"])
    return runner


def occupy(run: Run, runner: Runner, sessions: int = 1) -> list[Run]:
    """Unfinished runs pinned to the runner, copied from this one."""
    copies = []
    for index in range(sessions):
        copy = Run.objects.get(pk=run.pk)
        copy.pk = None
        copy.runner = runner
        copy.status = Run.Status.RUNNING
        # A job has one run per schedule slot.
        copy.scheduled_for = run.scheduled_for + timedelta(minutes=index + 1)
        copy.save()
        copies.append(copy)
    return copies


# Choosing among runners


def runner_row(
    pk: int, max_sessions: int | None, environments: list[str] | None
) -> Runner:
    return Runner(pk=pk, max_sessions=max_sessions, environments=environments)


def test_choose_prefers_the_most_free_sessions() -> None:
    small = runner_row(1, 2, None)
    large = runner_row(2, 4, None)

    assert choose_runner([small, large], {2: 1}, NODE) == large
    assert choose_runner([small, large], {2: 3}, NODE) == small


def test_choose_prefers_an_unlimited_runner_and_breaks_ties_by_age() -> None:
    limited = runner_row(1, 50, None)
    first = runner_row(2, None, None)
    second = runner_row(3, None, None)

    assert choose_runner([limited, second, first], {}, NODE) == first


def test_choose_skips_full_runners_and_missing_environments() -> None:
    full = runner_row(1, 1, [NODE])
    python_only = runner_row(2, None, ["python"])

    assert choose_runner([full, python_only], {1: 1}, NODE) is None
    # Every runner offers its own default environment.
    assert choose_runner([full, python_only], {1: 1}, "default") == python_only


def test_a_runner_that_has_not_answered_may_take_any_environment() -> None:
    unknown = runner_row(1, None, None)

    assert choose_runner([unknown], {}, NODE) == unknown


# Pinning a run


def test_the_configured_runner_takes_runs_on_a_new_install(run: Run) -> None:
    assert assign_runner(run.pk).name == "default"


@pytest.mark.django_db
def test_runs_spread_to_the_runner_with_room(run: Run) -> None:
    default = sync_configured_runner()
    assert default is not None
    default.max_sessions = 1
    default.save(update_fields=["max_sessions"])
    occupy(run, default)
    other = make_runner("runner-b", max_sessions=2)

    assert assign_runner(run.pk) == other
    run.refresh_from_db()
    assert run.runner_id == other.pk


def test_finished_runs_free_their_session(run: Run) -> None:
    only = make_runner("runner-b", max_sessions=1)
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    for busy in occupy(run, only):
        Run.objects.filter(pk=busy.pk).update(status=Run.Status.SUCCEEDED)

    assert assign_runner(run.pk) == only


def test_draining_runners_and_other_pools_take_no_new_runs(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    make_runner("gpu-1", pool="gpu")
    usable = make_runner("runner-c")

    assert assign_runner(run.pk) == usable


def test_a_run_goes_only_where_its_environment_is(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    make_runner("python-only", environments=["python"])
    node = make_runner("node-runner", environments=["python", NODE])

    assert assign_runner(run.pk) == node


def test_a_pinned_run_keeps_its_runner_even_when_it_drains(run: Run) -> None:
    pinned = make_runner("runner-b")
    update_run(run.pk, runner=pinned)
    change_runner_state("runner-b", Runner.State.DRAINING)

    assert assign_runner(run.pk) == pinned


# Waiting and failing


def test_a_run_waits_for_a_free_session(run: Run) -> None:
    default = sync_configured_runner()
    assert default is not None
    default.max_sessions = 1
    default.save(update_fields=["max_sessions"])
    (busy,) = occupy(run, default)
    sleeps: list[float] = []

    def finish_the_busy_run(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            Run.objects.filter(pk=busy.pk).update(status=Run.Status.SUCCEEDED)

    assert assign_runner(run.pk, sleep=finish_the_busy_run) == default
    assert sleeps == [runners.CAPACITY_POLL_SECONDS] * 2
    waits = [e for e in run_events(run) if e.message.startswith("Waiting")]
    assert len(waits) == 1


def test_a_run_waits_while_every_runner_drains(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)

    def reactivate(seconds: float) -> None:
        change_runner_state("default", Runner.State.ACTIVE)

    assert assign_runner(run.pk, sleep=reactivate).name == "default"


def test_waiting_stops_at_the_run_time_limit(run: Run) -> None:
    Run.objects.filter(pk=run.pk).update(started_at=timezone.now() - timedelta(days=30))
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)

    with pytest.raises(RuntimeError, match="time limit"):
        assign_runner(run.pk, sleep=lambda seconds: None)
    run.refresh_from_db()
    assert run.runner_id is None


def test_a_run_no_runner_can_take_fails_at_once(run: Run) -> None:
    default = sync_configured_runner()
    assert default is not None
    default.environments = ["python"]
    default.save(update_fields=["environments"])

    def never(seconds: float) -> None:
        raise AssertionError("A run no runner offers must not wait.")

    with pytest.raises(RuntimeError, match="No runner offers the node environment"):
        assign_runner(run.pk, sleep=never)


def test_a_run_fails_when_no_runner_is_in_the_default_pool(run: Run) -> None:
    default = sync_configured_runner()
    assert default is not None
    default.pool = "gpu"
    default.save(update_fields=["pool"])

    with pytest.raises(RuntimeError, match="default pool"):
        assign_runner(run.pk, sleep=lambda seconds: None)


# Learning each runner's environments


KEY = public_key_pem()


class EnvironmentsClient:
    answers: dict[str, RunnerEnvironments | Exception] = {}

    def __init__(self, runner: Runner) -> None:
        self.name = runner.name

    def __enter__(self) -> "EnvironmentsClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def current_provisioning_key(self, timeout: float | None = None) -> str:
        return KEY

    def environments(self, timeout: float | None = None) -> RunnerEnvironments:
        answer = self.answers[self.name]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.mark.django_db
def test_refresh_keeps_what_each_runner_offers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_runner("runner-b")
    make_runner("runner-c", environments=["python"])
    make_runner("runner-d", environments=["python"])
    change_runner_state("runner-d", Runner.State.DOWN)
    EnvironmentsClient.answers = {
        "runner-b": RunnerEnvironments(
            images={"python": "p", NODE: "n"}, default="python"
        ),
        "runner-c": RunnerProtocolError("down"),
    }
    monkeypatch.setattr(runners, "runner_client", EnvironmentsClient)

    assert refresh_runners() == 1

    assert find_runner("runner-b").environments == [NODE, "python"]
    # Its key is pinned at first contact.
    assert find_runner("runner-b").provisioning_key == KEY
    assert find_runner("runner-c").provisioning_key == ""
    # A runner that did not answer keeps its last answer.
    assert find_runner("runner-c").environments == ["python"]
    # A down runner is not asked.
    assert find_runner("runner-d").environments == ["python"]


# Keeping a subscription login on one runner

LOGIN = "6f1c1c2e-8f43-4d5c-9a8e-3c0b7f0d2a11"


def with_login(run: Run, key: str = LOGIN) -> Run:
    run.execution_plan["execution"]["credential_key"] = key
    run.save(update_fields=["execution_plan"])
    return run


def another_run(run: Run, minutes: int) -> Run:
    copy = Run.objects.get(pk=run.pk)
    copy.pk = None
    copy.runner = None
    copy.scheduled_for = run.scheduled_for + timedelta(minutes=minutes)
    copy.save()
    return copy


def test_a_login_stays_on_its_first_runner(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    first = make_runner("runner-b", max_sessions=1)
    with_login(run)

    assert assign_runner(run.pk) == first
    assert login_home(LOGIN).runner == first

    # runner-c has more room, but the login's copy lives on runner-b.
    make_runner("runner-c")
    Run.objects.filter(pk=run.pk).update(status=Run.Status.SUCCEEDED)
    assert assign_runner(another_run(run, 1).pk) == first


def test_a_login_waits_for_its_home_instead_of_moving(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    home = make_runner("runner-b", max_sessions=1)
    make_runner("runner-c")
    set_login_home(LOGIN, home)
    occupy(run, home)

    assert try_assign_runner(with_login(run).pk, NODE, LOGIN) is None


def test_a_login_moves_when_its_home_drains(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    old = make_runner("runner-b")
    new = make_runner("runner-c")
    set_login_home(LOGIN, old)
    change_runner_state("runner-b", Runner.State.DRAINING)

    assert assign_runner(with_login(run).pk) == new
    assert login_home(LOGIN).runner == new


def test_a_login_moves_when_its_home_lacks_the_environment(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    old = make_runner("runner-b", environments=["python"])
    new = make_runner("runner-c", environments=[NODE])
    set_login_home(LOGIN, old)

    assert assign_runner(with_login(run).pk) == new


def test_runs_without_a_login_set_no_home(run: Run) -> None:
    assign_runner(run.pk)

    assert not LoginHome.objects.exists()
