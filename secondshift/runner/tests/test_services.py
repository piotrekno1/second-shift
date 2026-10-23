from pathlib import Path
from unittest.mock import Mock

import pytest

from secondshift.automations.runner_protocol import CommandRequest, RunnerProtocolError
from secondshift.runner.models import RunnerCommand, RunnerSession
from secondshift.runner.services import DockerRunnerService, RunnerConfigurationError


def active_session(tmp_path: Path) -> RunnerSession:
    return RunnerSession.objects.create(
        session_id="run-7",
        run_id=7,
        repository="example/project",
        commit_sha="a" * 40,
        image="secondshift-runner:test",
        container_name="secondshift-run-7",
        workspace_path=str(tmp_path / "sessions" / "run-7" / "workspace"),
        status=RunnerSession.Status.ACTIVE,
    )


@pytest.mark.django_db
def test_command_reservation_is_idempotent_and_fails_closed(
    tmp_path: Path,
) -> None:
    active_session(tmp_path)
    service = DockerRunnerService()
    request = CommandRequest("call-1", ("git", "status"))

    assert service.reserve_command("run-7", request) is None

    with pytest.raises(RunnerProtocolError, match="outcome is unknown"):
        service.reserve_command("run-7", request)

    command = RunnerCommand.objects.get()
    command.status = RunnerCommand.Status.COMPLETED
    command.exit_code = 0
    command.output = "clean"
    command.save()
    cached = service.reserve_command("run-7", request)

    assert cached is not None
    assert cached.output == "clean"


@pytest.mark.django_db
def test_call_id_cannot_be_reused_with_different_arguments(tmp_path: Path) -> None:
    active_session(tmp_path)
    service = DockerRunnerService()
    service.reserve_command("run-7", CommandRequest("call-1", ("git", "status")))

    with pytest.raises(RunnerProtocolError, match="different arguments"):
        service.reserve_command("run-7", CommandRequest("call-1", ("git", "diff")))


def test_container_flags_are_server_owned(settings, tmp_path: Path) -> None:
    settings.RUNNER_RUNTIME = "runsc"
    settings.RUNNER_PIDS_LIMIT = 64
    settings.RUNNER_MEMORY = "1g"
    settings.RUNNER_CPUS = "1"
    settings.RUNNER_SANDBOX_UID = 65534
    settings.RUNNER_SANDBOX_GID = 65534
    spec = Mock(image="secondshift-runner:test")

    command = DockerRunnerService.create_command(
        spec, "secondshift-run-7", tmp_path / "workspace"
    )

    assert "--runtime" in command
    assert "runsc" in command
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--user=65534:65534" in command
    mounts = [
        part for part in command if part.startswith(("--mount", "-v", "--volume"))
    ]
    assert mounts == [f"--mount=type=bind,src={tmp_path / 'workspace'},dst=/workspace"]
    assert not any(part.startswith("--env-file") for part in command)
    assert [part for part in command if part.startswith("--env")] == [
        "--env=HOME=/tmp",
        "--env=PYTHONDONTWRITEBYTECODE=1",
    ]
    assert command[-3:] == ["secondshift-runner:test", "sleep", "infinity"]


def completed(code: int, stderr: str = "") -> Mock:
    return Mock(returncode=code, stdout="", stderr=stderr)


def test_runner_pulls_a_missing_sandbox_image(settings, monkeypatch) -> None:
    settings.RUNNER_IMAGE = "python:3.13-slim@sha256:abc"
    calls: list[list[str]] = []

    def run(command: list[str], timeout: int = 30) -> Mock:
        calls.append(command)
        return completed(1 if command[1] == "image" else 0)

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))
    DockerRunnerService().ensure_image()

    assert calls == [
        ["docker", "image", "inspect", "python:3.13-slim@sha256:abc"],
        ["docker", "pull", "python:3.13-slim@sha256:abc"],
    ]


def test_runner_skips_the_pull_when_the_image_is_present(settings, monkeypatch) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], timeout: int = 30) -> Mock:
        calls.append(command)
        return completed(0)

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))
    DockerRunnerService().ensure_image()

    assert [command[1] for command in calls] == ["image"]


def test_runner_refuses_to_start_when_the_pull_fails(settings, monkeypatch) -> None:
    settings.RUNNER_IMAGE = "registry.example/sandbox:1"

    def run(command: list[str], timeout: int = 30) -> Mock:
        return completed(1, "Error response from daemon: pull access denied")

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))

    with pytest.raises(RunnerConfigurationError, match="pull access denied"):
        DockerRunnerService().ensure_image()
