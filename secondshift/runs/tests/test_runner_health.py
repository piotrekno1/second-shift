"""Checking runners, and what happens to runs on one that stops answering."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from secondshift.runner_protocol.environments import RunnerEnvironments
from secondshift.runner_protocol.sessions import (
    RunnerProtocolError,
    RunnerUnreachable,
    session_id_for,
)
from secondshift.runner_protocol.tests.keys import public_key_pem
from secondshift.runs import runner_health
from secondshift.runs.dao import find_runner, login_home, run_events, set_login_home
from secondshift.runs.models import Run, RunFailure, Runner, SessionCleanup
from secondshift.runs.reporting.failures import stage_failure
from secondshift.runs.runner_health import (
    LOST_SECONDS,
    fail_runs_on_lost_runners,
    refresh_runners,
)
from secondshift.runs.runners import (
    UNREACHABLE_SECONDS,
    add_runner,
    assign_runner,
    change_runner_state,
    sync_configured_runner,
)
from secondshift.runs.tests.test_workflows import execution_run

TOKEN = "t" * 32
KEY = public_key_pem()
LOGIN = "6f1c1c2e-8f43-4d5c-9a8e-3c0b7f0d2a11"


@pytest.fixture
def run(db: None, settings: Any) -> Run:
    settings.RUNNER_SERVICE_URL = "http://runner-a:8002"
    settings.RUNNER_SERVICE_TOKEN = TOKEN
    return execution_run(db)


def make_runner(name: str, environments: list[str] | None = None) -> Runner:
    runner = add_runner(name, f"https://{name}:8002", f"{name}-token".ljust(32, "x"))
    runner.environments = environments
    runner.save(update_fields=["environments"])
    return runner


def failing_for(name: str, seconds: int) -> None:
    Runner.objects.filter(name=name).update(
        failing_since=timezone.now() - timedelta(seconds=seconds)
    )


class FakeRunners:
    """Runner clients by name. A name missing from `answers` does not answer."""

    def __init__(self) -> None:
        self.answers: dict[str, RunnerEnvironments] = {}
        self.stopped: list[str] = []

    def client(self, runner: Runner) -> "FakeClient":
        return FakeClient(self, runner.name)


class FakeClient:
    def __init__(self, runners: FakeRunners, name: str) -> None:
        self.runners = runners
        self.name = name

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def answer(self) -> RunnerEnvironments:
        if self.name not in self.runners.answers:
            raise RunnerUnreachable("The runner service could not be reached.")
        return self.runners.answers[self.name]

    def environments(self, timeout: float | None = None) -> RunnerEnvironments:
        return self.answer()

    def current_provisioning_key(self, timeout: float | None = None) -> str:
        self.answer()
        return KEY

    def stop(self, session_id: str) -> None:
        self.answer()
        self.runners.stopped.append(session_id)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRunners:
    runners = FakeRunners()
    monkeypatch.setattr(runner_health, "runner_client", runners.client)
    return runners


# Checking


@pytest.mark.django_db
def test_a_check_keeps_what_each_runner_offers(fake: FakeRunners) -> None:
    make_runner("runner-b")
    make_runner("runner-c", environments=["python"])
    make_runner("runner-d", environments=["python"])
    change_runner_state("runner-d", Runner.State.DOWN)
    fake.answers = {
        "runner-b": RunnerEnvironments(
            images={"python": "p", "node": "n"}, default="python"
        )
    }

    assert refresh_runners() == 1

    answering = find_runner("runner-b")
    assert answering.environments == ["node", "python"]
    # Its key is pinned at first contact.
    assert answering.provisioning_key == KEY
    assert answering.failing_since is None
    silent = find_runner("runner-c")
    assert silent.provisioning_key == ""
    # A runner that did not answer keeps its last answer, and is failing.
    assert silent.environments == ["python"]
    assert silent.failing_since is not None
    # A runner the operator marked down is not asked.
    assert find_runner("runner-d").failing_since is None


@pytest.mark.django_db
def test_failing_keeps_its_first_time_and_clears_on_an_answer(
    fake: FakeRunners,
) -> None:
    make_runner("runner-b")
    failing_for("runner-b", 300)
    first = find_runner("runner-b").failing_since

    refresh_runners()
    assert find_runner("runner-b").failing_since == first

    fake.answers["runner-b"] = RunnerEnvironments(images={}, default="")
    refresh_runners()
    assert find_runner("runner-b").failing_since is None


@pytest.mark.django_db
def test_a_drained_runner_that_comes_back_stays_drained(fake: FakeRunners) -> None:
    make_runner("runner-b")
    change_runner_state("runner-b", Runner.State.DRAINING)
    failing_for("runner-b", 300)
    fake.answers["runner-b"] = RunnerEnvironments(images={}, default="")

    refresh_runners()

    assert find_runner("runner-b").state == Runner.State.DRAINING


# Placement


def test_a_runner_that_stopped_answering_takes_no_new_runs(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    make_runner("runner-b")
    other = make_runner("runner-c")
    failing_for("runner-b", UNREACHABLE_SECONDS + 1)

    assert assign_runner(run.pk) == other


def test_one_missed_check_does_not_stop_placement(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    only = make_runner("runner-b")
    failing_for("runner-b", 30)

    assert assign_runner(run.pk) == only


def test_a_run_waits_while_its_only_runner_does_not_answer(run: Run) -> None:
    sync_configured_runner()
    failing_for("default", UNREACHABLE_SECONDS + 1)

    def answers_again(seconds: float) -> None:
        Runner.objects.filter(name="default").update(failing_since=None)

    assert assign_runner(run.pk, sleep=answers_again).name == "default"


def test_a_login_leaves_a_home_that_stopped_answering(run: Run) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    old = make_runner("runner-b")
    new = make_runner("runner-c")
    set_login_home(LOGIN, old)
    failing_for("runner-b", UNREACHABLE_SECONDS + 1)
    run.execution_plan["execution"]["credential_key"] = LOGIN
    run.save(update_fields=["execution_plan"])

    assert assign_runner(run.pk) == new
    assert login_home(LOGIN).runner == new


# Runs on a lost runner


def pinned_running(run: Run, runner: Runner) -> Run:
    Run.objects.filter(pk=run.pk).update(runner=runner, status=Run.Status.RUNNING)
    run.refresh_from_db()
    return run


def test_runs_on_a_lost_runner_fail_and_keep_their_session(run: Run) -> None:
    lost = make_runner("runner-b")
    pinned_running(run, lost)
    failing_for("runner-b", LOST_SECONDS + 1)

    assert fail_runs_on_lost_runners() == 1
    # A second pass changes nothing.
    assert fail_runs_on_lost_runners() == 0

    run.refresh_from_db()
    assert run.status == Run.Status.FAILED
    assert run.failure == RunFailure.RUNNER
    assert any("runner-b stopped answering" in e.message for e in run_events(run))
    cleanup = SessionCleanup.objects.get()
    assert cleanup.session_id == session_id_for(run.pk, run.runner_session_key.hex)
    assert cleanup.runner == lost


def test_runs_survive_a_short_outage(run: Run) -> None:
    pinned_running(run, make_runner("runner-b"))
    failing_for("runner-b", UNREACHABLE_SECONDS + 1)

    assert fail_runs_on_lost_runners() == 0
    run.refresh_from_db()
    assert run.status == Run.Status.RUNNING


def test_finished_runs_on_a_lost_runner_are_left_alone(run: Run) -> None:
    pinned_running(run, make_runner("runner-b"))
    Run.objects.filter(pk=run.pk).update(status=Run.Status.SUCCEEDED)
    failing_for("runner-b", LOST_SECONDS + 1)

    assert fail_runs_on_lost_runners() == 0
    assert not SessionCleanup.objects.exists()


def test_lost_sessions_stop_when_the_runner_answers_again(
    run: Run, fake: FakeRunners
) -> None:
    pinned_running(run, make_runner("runner-b"))
    failing_for("runner-b", LOST_SECONDS + 1)

    # Still lost: its runs fail, and their sessions wait.
    refresh_runners()
    assert fake.stopped == []
    assert SessionCleanup.objects.exists()

    fake.answers["runner-b"] = RunnerEnvironments(images={}, default="")
    refresh_runners()

    run.refresh_from_db()
    assert fake.stopped == [session_id_for(run.pk, run.runner_session_key.hex)]
    assert not SessionCleanup.objects.exists()


# Failure reasons


def test_a_runner_that_cannot_be_reached_is_named_as_the_cause() -> None:
    lost = stage_failure(
        RunFailure.SANDBOX, RunnerUnreachable("The runner did not answer in time.")
    )
    refused = stage_failure(RunFailure.SANDBOX, RunnerProtocolError("no such image"))

    assert lost.kind == RunFailure.RUNNER
    assert lost.reason == "The runner did not answer in time."
    assert refused.kind == RunFailure.SANDBOX


def test_a_control_plane_clock_that_is_off_does_not_move_the_thresholds(
    run: Run, fake: FakeRunners, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_configured_runner()
    change_runner_state("default", Runner.State.DRAINING)
    runner = make_runner("runner-b")
    pinned_running(run, runner)
    # The worker found it failing just now.
    refresh_runners()
    # Another host, whose clock runs an hour ahead, reads that.
    ahead = timezone.now() + timedelta(hours=1)
    monkeypatch.setattr(timezone, "now", lambda: ahead)

    # Ten minutes have not passed by the database's clock, so its run goes on.
    assert fail_runs_on_lost_runners() == 0
    # Nor have two, so it still takes runs.
    Run.objects.filter(pk=run.pk).update(status=Run.Status.SUCCEEDED)
    assert assign_runner(another_run(run).pk) == runner


def another_run(run: Run) -> Run:
    copy = Run.objects.get(pk=run.pk)
    copy.pk = None
    copy.runner = None
    copy.status = Run.Status.QUEUED
    copy.scheduled_for = run.scheduled_for + timedelta(minutes=1)
    copy.save()
    return copy
