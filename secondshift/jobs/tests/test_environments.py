import pytest

from secondshift.jobs import environments
from secondshift.runner.tests.fakes import sandboxes
from secondshift.runner_protocol.environments import RunnerEnvironments
from secondshift.runner_protocol.sessions import RunnerProtocolError


def test_the_control_plane_asks_the_runner_once_a_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = [sandboxes("python:3.13-slim"), sandboxes("other")]
    monkeypatch.setattr(environments, "fetch_environments", lambda: answers.pop(0))
    clock = [100.0]
    monkeypatch.setattr(environments.time, "monotonic", lambda: clock[0])

    assert environments.runner_environments() == sandboxes("python:3.13-slim")
    clock[0] += 59
    assert environments.runner_environments() == sandboxes("python:3.13-slim")
    clock[0] += 2
    assert environments.runner_environments() == sandboxes("other")


def test_an_unreachable_runner_blocks_no_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable() -> RunnerEnvironments:
        raise RunnerProtocolError("The runner service could not be reached.")

    monkeypatch.setattr(environments, "fetch_environments", unreachable)

    assert environments.runner_environments() is None
    assert environments.environment_missing("python_node") is False


def test_an_environment_the_runner_lacks_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        environments,
        "fetch_environments",
        lambda: sandboxes("python:3.13-slim", node="node:22"),
    )

    assert environments.environment_missing("default") is False
    assert environments.environment_missing("python") is False
    assert environments.environment_missing("node") is False
    assert environments.environment_missing("browser") is True
