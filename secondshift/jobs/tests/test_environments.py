"""Which environments the runners offer, as the registry last heard."""

import pytest

from secondshift.jobs.environments import environment_offer, runner_environments
from secondshift.jobs.tests.fixtures import offer_environments
from secondshift.runner.tests.fakes import sandboxes
from secondshift.runs.models import Runner

pytestmark = pytest.mark.django_db


def missing(name: str) -> bool:
    return environment_offer().missing(name)


def test_nothing_is_missing_before_any_runner_is_registered() -> None:
    assert missing("browser") is False


def test_an_environment_no_runner_offers_is_missing() -> None:
    offer_environments(sandboxes("python:3.13-slim", node="node:22"))

    assert missing("default") is False
    assert missing("python") is False
    assert missing("node") is False
    assert missing("browser") is True


def test_any_runner_that_takes_runs_may_offer_it() -> None:
    offer_environments(sandboxes("python:3.13-slim"), name="runner-a")
    offer_environments(sandboxes("python:3.13-slim", node="node:22"), name="runner-b")
    # A drained runner may come back, so its environments still count.
    Runner.objects.filter(name="runner-b").update(state=Runner.State.DRAINING)
    offer_environments(sandboxes("python:3.13-slim", browser="b"), name="runner-c")
    # Runners in another pool take no runs yet.
    Runner.objects.filter(name="runner-c").update(pool="gpu")

    assert missing("node") is False
    assert missing("browser") is True


def test_a_runner_that_has_not_answered_may_offer_anything() -> None:
    offer_environments(sandboxes("python:3.13-slim"))
    Runner.objects.update(environments=None, images=None)

    assert missing("browser") is False
    assert runner_environments() == []


def test_each_runner_that_answered_is_listed() -> None:
    offer_environments(sandboxes("python:3.13-slim"), name="runner-a")
    offer_environments(sandboxes("other", node="node:22"), name="runner-b")

    assert [(item.runner, item.environments) for item in runner_environments()] == [
        ("runner-a", sandboxes("python:3.13-slim")),
        ("runner-b", sandboxes("other", node="node:22")),
    ]
