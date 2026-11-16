import subprocess
from typing import Any
from unittest.mock import Mock

import pytest

from secondshift.docker import commands
from secondshift.docker.commands import DockerError

IMAGE_ID = "sha256:" + "a" * 64


def answer(
    monkeypatch: Any, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> Mock:
    run = Mock(return_value=Mock(returncode=returncode, stdout=stdout, stderr=stderr))
    monkeypatch.setattr(commands.subprocess, "run", run)
    return run


def fail_with(monkeypatch: Any, error: Exception) -> None:
    monkeypatch.setattr(commands.subprocess, "run", Mock(side_effect=error))


def test_resolves_a_tag_to_its_immutable_id(monkeypatch: Any) -> None:
    run = answer(monkeypatch, stdout=IMAGE_ID + "\n")

    assert commands.resolve_image_id("secondshift-agent:dev") == IMAGE_ID
    assert run.call_args.args[0] == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{.Id}}",
        "secondshift-agent:dev",
    ]


def test_rejects_an_image_id_that_is_not_a_digest(monkeypatch: Any) -> None:
    answer(monkeypatch, stdout="latest\n")

    with pytest.raises(DockerError, match="immutable"):
        commands.resolve_image_id("secondshift-agent:dev")


def test_a_failure_keeps_dockers_last_line_as_the_reason(monkeypatch: Any) -> None:
    answer(monkeypatch, returncode=1, stderr="step 1\nnetwork unreachable\n\n")

    with pytest.raises(DockerError) as caught:
        commands.output("build", ".")

    assert str(caught.value) == "docker build failed."
    assert caught.value.reason == "network unreachable"


def test_the_reason_falls_back_to_the_message() -> None:
    assert DockerError("docker info timed out.").reason == "docker info timed out."


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired("docker", 15), FileNotFoundError("docker")],
)
def test_docker_is_unavailable_when_it_cannot_answer(
    monkeypatch: Any, error: Exception
) -> None:
    fail_with(monkeypatch, error)

    assert not commands.available()
    assert not commands.is_docker_desktop()
    with pytest.raises(DockerError):
        commands.output("info")


def test_docker_is_unavailable_when_the_daemon_is_down(monkeypatch: Any) -> None:
    answer(monkeypatch, returncode=1, stderr="Cannot connect to the Docker daemon")

    assert not commands.available()


@pytest.mark.parametrize(
    ("operating_system", "expected"),
    [("Docker Desktop", True), ("Ubuntu 24.04 LTS", False)],
)
def test_recognizes_docker_desktop(
    monkeypatch: Any, operating_system: str, expected: bool
) -> None:
    answer(monkeypatch, stdout=operating_system + "\n")

    assert commands.is_docker_desktop() is expected
