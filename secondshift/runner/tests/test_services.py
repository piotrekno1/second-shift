from pathlib import Path
from unittest.mock import Mock

import pytest

from secondshift.runner.models import RunnerCommand, RunnerSession
from secondshift.runner.services import (
    DockerRunnerService,
    RunnerConfigurationError,
)
from secondshift.runner.tests.fakes import sandboxes
from secondshift.runner_protocol.sessions import (
    CommandRequest,
    CommandResult,
    RunnerProtocolError,
    RunnerSessionSpec,
)


def active_session(tmp_path: Path) -> RunnerSession:
    return RunnerSession.objects.create(
        session_id="run-7",
        run_id=7,
        repository="example/project",
        commit_sha="a" * 40,
        image="secondshift-runner:test",
        source_sha256="d" * 64,
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
    command = DockerRunnerService.create_command(
        "secondshift-runner:test", "secondshift-run-7", tmp_path / "workspace"
    )

    assert "--runtime" in command
    assert "runsc" in command
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--memory=1g" in command
    assert "--memory-swap=1g" in command
    assert "--user=65534:65534" in command
    mounts = [
        part for part in command if part.startswith(("--mount", "-v", "--volume"))
    ]
    assert mounts == [
        f"--mount=type=bind,src={tmp_path / 'workspace'},dst=/workspace",
        f"--mount=type=bind,src={tmp_path / 'outputs'},dst=/outputs",
        f"--mount=type=bind,src={tmp_path / 'history'},dst=/history,readonly",
        f"--mount=type=bind,src={tmp_path / 'skill'},dst=/skill,readonly",
    ]
    assert not any(part.startswith("--env-file") for part in command)
    assert [part for part in command if part.startswith("--env")] == [
        "--env=HOME=/tmp",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "--env=GIT_CONFIG_COUNT=1",
        "--env=GIT_CONFIG_KEY_0=safe.directory",
        "--env=GIT_CONFIG_VALUE_0=/workspace",
    ]
    assert command[-3:] == ["secondshift-runner:test", "sleep", "infinity"]


def completed(code: int, stderr: str = "") -> Mock:
    return Mock(returncode=code, stdout="", stderr=stderr)


def test_runner_pulls_a_missing_sandbox_image(settings, monkeypatch) -> None:
    settings.RUNNER_ENVIRONMENTS = sandboxes("python:3.13-slim@sha256:abc")
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


def test_runner_pulls_every_environment_image(settings, monkeypatch) -> None:
    settings.RUNNER_ENVIRONMENTS = sandboxes(
        "python:3.13-slim@sha256:abc",
        browser="registry.example/browser@sha256:def",
        same="python:3.13-slim@sha256:abc",
    )
    pulled: list[str] = []

    def run(command: list[str], timeout: int = 30) -> Mock:
        if command[1] == "pull":
            pulled.append(command[2])
        return completed(1 if command[1] == "image" else 0)

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))
    DockerRunnerService().ensure_image()

    assert pulled == [
        "python:3.13-slim@sha256:abc",
        "registry.example/browser@sha256:def",
    ]


def test_runner_refuses_an_environment_it_does_not_have(
    settings, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    settings.RUNNER_ENVIRONMENTS = sandboxes(
        "secondshift-runner:test", node="registry.example/node@sha256:def"
    )
    spec = RunnerSessionSpec(
        run_id=7,
        session_key="0" * 32,
        repository="example/project",
        commit_sha="a" * 40,
        environment="browser",
        source_sha256="d" * 64,
    )

    with pytest.raises(RunnerProtocolError, match="has no browser environment"):
        DockerRunnerService().start(spec)


def test_the_runner_alone_maps_environments_to_images(settings) -> None:
    settings.RUNNER_ENVIRONMENTS = sandboxes(
        "secondshift-runner:test", node="registry.example/node@sha256:def"
    )

    offered = settings.RUNNER_ENVIRONMENTS
    assert offered.image("default") == "secondshift-runner:test"
    assert offered.image("python") == "secondshift-runner:test"
    assert offered.image("node") == "registry.example/node@sha256:def"
    assert offered.image("browser") is None


def test_every_environment_gets_the_same_flags_and_mounts(
    settings, tmp_path: Path
) -> None:
    settings.RUNNER_ENVIRONMENTS = sandboxes(
        "secondshift-runner:test", node="registry.example/node@sha256:def"
    )
    commands = [
        DockerRunnerService.create_command(
            image, "secondshift-run-7", tmp_path / "workspace"
        )
        for image in ("secondshift-runner:test", "registry.example/node@sha256:def")
    ]

    assert commands[0][-3:] == ["secondshift-runner:test", "sleep", "infinity"]
    assert commands[1][-3:] == [
        "registry.example/node@sha256:def",
        "sleep",
        "infinity",
    ]
    assert commands[0][:-3] == commands[1][:-3]


def test_runner_skips_the_pull_when_the_image_is_present(settings, monkeypatch) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], timeout: int = 30) -> Mock:
        calls.append(command)
        return completed(0)

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))
    DockerRunnerService().ensure_image()

    assert [command[1] for command in calls] == ["image"]


def test_runner_refuses_to_start_when_the_pull_fails(settings, monkeypatch) -> None:
    settings.RUNNER_ENVIRONMENTS = sandboxes("registry.example/sandbox:1")

    def run(command: list[str], timeout: int = 30) -> Mock:
        return completed(1, "Error response from daemon: pull access denied")

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))

    with pytest.raises(RunnerConfigurationError, match="pull access denied"):
        DockerRunnerService().ensure_image()


@pytest.mark.django_db
def test_an_old_session_with_the_same_run_id_does_not_block_a_new_run(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    settings.RUNNER_ENVIRONMENTS = sandboxes("secondshift-runner:test")
    RunnerSession.objects.create(
        session_id="run-7",
        run_id=7,
        repository="old/project",
        commit_sha="b" * 40,
        image="secondshift-runner:test",
        source_sha256="d" * 64,
        container_name="secondshift-run-7",
        workspace_path=str(tmp_path / "sessions" / "run-7" / "workspace"),
        status=RunnerSession.Status.ERROR,
    )
    monkeypatch.setattr(
        DockerRunnerService, "run_command", staticmethod(lambda *a, **k: completed(0))
    )
    monkeypatch.setattr(
        DockerRunnerService, "prepare_workspace_permissions", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "secondshift.runner.services.unpack_source", lambda *a, **k: None
    )
    spec = RunnerSessionSpec(
        run_id=7,
        session_key="c" * 32,
        repository="example/project",
        commit_sha="a" * 40,
        environment="default",
        source_sha256="d" * 64,
    )

    session_id = DockerRunnerService().start(spec)

    assert session_id == spec.session_id
    session = RunnerSession.objects.get(session_id=session_id)
    assert session.status == RunnerSession.Status.ACTIVE
    assert session.container_name == f"secondshift-{spec.session_id}"
    assert RunnerSession.objects.filter(run_id=7).count() == 2


def fake_inspect(running: bool, oom_killed: bool):
    def run_command(command: list[str], timeout: int = 30) -> Mock:
        if command[:2] != ["docker", "inspect"]:
            return completed(0)
        state = {"{{.State.Running}}": running, "{{.State.OOMKilled}}": oom_killed}
        return Mock(returncode=0, stdout=f"{str(state[command[3]]).lower()}\n")

    return staticmethod(run_command)


@pytest.mark.django_db
def test_a_sandbox_killed_for_memory_ends_the_session(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    active_session(tmp_path)
    monkeypatch.setattr(
        "secondshift.runner.services.subprocess.run", lambda *a, **k: completed(128)
    )
    monkeypatch.setattr(
        DockerRunnerService, "run_command", fake_inspect(running=True, oom_killed=True)
    )

    with pytest.raises(RunnerProtocolError, match="ran out of memory"):
        DockerRunnerService().execute("run-7", CommandRequest("call-1", ("python",)))

    assert RunnerSession.objects.get().status == RunnerSession.Status.STOPPED


@pytest.mark.django_db
def test_a_failing_command_in_a_running_sandbox_is_returned(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    active_session(tmp_path)
    monkeypatch.setattr(
        "secondshift.runner.services.subprocess.run", lambda *a, **k: completed(2)
    )
    monkeypatch.setattr(
        DockerRunnerService, "run_command", fake_inspect(running=True, oom_killed=False)
    )

    result = DockerRunnerService().execute(
        "run-7", CommandRequest("call-1", ("grep", "x"))
    )

    assert result.exit_code == 2
    assert RunnerSession.objects.get().status == RunnerSession.Status.ACTIVE


@pytest.mark.django_db
def test_a_sandbox_that_stopped_ends_the_session(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    active_session(tmp_path)
    monkeypatch.setattr(
        "secondshift.runner.services.subprocess.run", lambda *a, **k: completed(1)
    )
    monkeypatch.setattr(
        DockerRunnerService,
        "run_command",
        fake_inspect(running=False, oom_killed=False),
    )

    with pytest.raises(RunnerProtocolError, match="stopped unexpectedly"):
        DockerRunnerService().execute("run-7", CommandRequest("call-1", ("true",)))

    assert RunnerSession.objects.get().status == RunnerSession.Status.STOPPED


@pytest.mark.django_db
def test_collecting_stops_the_sandbox_before_reading_files(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    session = active_session(tmp_path)
    steps: list[str] = []

    def run(command: list[str], timeout: int = 30) -> Mock:
        steps.append(" ".join(command[:2]))
        return Mock(returncode=0, stdout="false\n", stderr="")

    def collect(session_root: Path, workspace: Path) -> Path:
        steps.append("collect")
        assert workspace == Path(session.workspace_path)
        return session_root / "collection.tar.gz"

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))
    monkeypatch.setattr("secondshift.runner.services.collect", collect)

    DockerRunnerService().collect("run-7")

    assert steps == [
        "docker rm",
        "docker rm",
        "docker kill",
        "docker inspect",
        "collect",
    ]


@pytest.mark.django_db
def test_a_sandbox_that_keeps_running_is_not_collected(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    active_session(tmp_path)
    monkeypatch.setattr(DockerRunnerService, "run_command", fake_inspect(True, False))
    monkeypatch.setattr(
        "secondshift.runner.services.collect", Mock(side_effect=AssertionError)
    )

    with pytest.raises(RunnerProtocolError, match="could not stop the sandbox"):
        DockerRunnerService().collect("run-7")


@pytest.mark.django_db
def test_an_ended_session_has_nothing_to_collect(settings, tmp_path: Path) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    session = active_session(tmp_path)
    session.status = RunnerSession.Status.STOPPED
    session.save()

    with pytest.raises(RunnerProtocolError, match="files are gone"):
        DockerRunnerService().collect("run-7")
    with pytest.raises(RunnerProtocolError, match="Unknown"):
        DockerRunnerService().collect("run-8")


@pytest.mark.django_db
@pytest.mark.parametrize("collect_changes", [False, True])
def test_the_baseline_is_recorded_only_when_the_spec_asks(
    settings, monkeypatch, tmp_path: Path, collect_changes: bool
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    settings.RUNNER_ENVIRONMENTS = sandboxes("secondshift-runner:test")
    recorded: list[Path] = []
    monkeypatch.setattr(
        DockerRunnerService, "run_command", staticmethod(lambda *a, **k: completed(0))
    )
    monkeypatch.setattr(
        DockerRunnerService, "prepare_workspace_permissions", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "secondshift.runner.services.unpack_source", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "secondshift.runner.services.record_baseline",
        lambda root, workspace: recorded.append(root),
    )
    spec = RunnerSessionSpec(
        run_id=7,
        session_key="c" * 32,
        repository="example/project",
        commit_sha="a" * 40,
        environment="default",
        source_sha256="d" * 64,
        collect_changes=collect_changes,
    )

    DockerRunnerService().start(spec)

    assert bool(recorded) is collect_changes
    # The sandbox's own repository is there either way.
    workspace = tmp_path / "sessions" / spec.session_id / "workspace"
    assert (workspace / ".git" / "HEAD").is_file()
    session = RunnerSession.objects.get(session_id=spec.session_id)
    assert session.collect_changes is collect_changes
    # A retry that asks differently is a different session, and is refused.
    other = RunnerSessionSpec(
        **{**spec.__dict__, "collect_changes": not collect_changes}
    )
    with pytest.raises(RunnerProtocolError, match="different session spec"):
        DockerRunnerService().start(other)


@pytest.mark.django_db
def test_earlier_files_are_unpacked_beside_the_workspace(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    settings.RUNNER_ENVIRONMENTS = sandboxes("secondshift-runner:test")
    unpacked: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        DockerRunnerService, "run_command", staticmethod(lambda *a, **k: completed(0))
    )
    monkeypatch.setattr(
        DockerRunnerService, "prepare_workspace_permissions", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "secondshift.runner.services.unpack_source",
        lambda sha256, target: unpacked.append((sha256, target)),
    )
    spec = RunnerSessionSpec(
        run_id=7,
        session_key="c" * 32,
        repository="example/project",
        commit_sha="a" * 40,
        environment="default",
        source_sha256="d" * 64,
        history_sha256="e" * 64,
    )

    DockerRunnerService().start(spec)

    root = tmp_path / "sessions" / spec.session_id
    assert unpacked == [("d" * 64, root / "workspace"), ("e" * 64, root / "history")]
    session = RunnerSession.objects.get(session_id=spec.session_id)
    assert session.history_sha256 == "e" * 64
    other = RunnerSessionSpec(**{**spec.__dict__, "history_sha256": "f" * 64})
    with pytest.raises(RunnerProtocolError, match="different session spec"):
        DockerRunnerService().start(other)


@pytest.mark.django_db
def test_a_skill_from_another_repository_is_unpacked_beside_the_workspace(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    settings.RUNNER_ENVIRONMENTS = sandboxes("secondshift-runner:test")
    unpacked: list[tuple[str, Path]] = []
    commands: list[list[str]] = []

    def run(command: list[str], timeout: int = 30) -> Mock:
        commands.append(command)
        return completed(0)

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))
    monkeypatch.setattr(
        DockerRunnerService, "prepare_workspace_permissions", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "secondshift.runner.services.unpack_source",
        lambda sha256, target: unpacked.append((sha256, target)),
    )
    spec = RunnerSessionSpec(
        run_id=7,
        session_key="c" * 32,
        repository="example/project",
        commit_sha="a" * 40,
        environment="default",
        source_sha256="d" * 64,
        collect_changes=True,
        skill_sha256="e" * 64,
    )

    DockerRunnerService().start(spec)

    root = tmp_path / "sessions" / spec.session_id
    # Never inside the workspace, so it is not part of the agent's changes.
    assert unpacked == [("d" * 64, root / "workspace"), ("e" * 64, root / "skill")]
    create = next(command for command in commands if command[1] == "create")
    assert f"--mount=type=bind,src={root / 'skill'},dst=/skill,readonly" in create
    assert "--network=none" in create
    session = RunnerSession.objects.get(session_id=spec.session_id)
    assert session.skill_sha256 == "e" * 64
    other = RunnerSessionSpec(**{**spec.__dict__, "skill_sha256": "f" * 64})
    with pytest.raises(RunnerProtocolError, match="different session spec"):
        DockerRunnerService().start(other)


def run_for(
    monkeypatch, seconds: float, result: CommandResult, commands: list[list[str]]
) -> None:
    """Make run_captured return this result after this many seconds."""
    clock = iter([100.0, 100.0 + seconds])
    monkeypatch.setattr(
        "secondshift.runner.services.time.monotonic", lambda: next(clock)
    )

    def run_captured(self, call_id: str, command: list[str], timeout: int):
        commands.append([*command, f"runner-timeout={timeout}"])
        return result

    monkeypatch.setattr(DockerRunnerService, "run_captured", run_captured)
    monkeypatch.setattr(
        DockerRunnerService, "run_command", fake_inspect(running=True, oom_killed=False)
    )


@pytest.mark.django_db
def test_a_command_past_its_limit_ends_inside_the_sandbox(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    session = active_session(tmp_path)
    session.command_timeout = True
    session.save()
    commands: list[list[str]] = []
    run_for(monkeypatch, 45.2, CommandResult("call-1", 124, "partial"), commands)

    result = DockerRunnerService().execute(
        "run-7", CommandRequest("call-1", ("npm", "test"), timeout_seconds=45)
    )

    assert result.timed_out is True
    assert result.output == "partial"
    assert commands == [
        [
            "docker",
            "exec",
            "--workdir",
            "/workspace",
            "secondshift-run-7",
            "timeout",
            "-k",
            "5",
            "45",
            "npm",
            "test",
            "runner-timeout=60",
        ]
    ]
    # The agent keeps its sandbox and can try something else.
    assert RunnerSession.objects.get().status == RunnerSession.Status.ACTIVE
    assert RunnerCommand.objects.get().timed_out is True


@pytest.mark.django_db
def test_a_command_that_fails_before_its_limit_did_not_time_out(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    session = active_session(tmp_path)
    session.command_timeout = True
    session.save()
    run_for(monkeypatch, 3, CommandResult("call-1", 124, ""), [])

    result = DockerRunnerService().execute(
        "run-7", CommandRequest("call-1", ("sh", "-c", "exit 124"), timeout_seconds=45)
    )

    assert result.timed_out is False
    assert result.exit_code == 124


@pytest.mark.django_db
def test_a_command_that_outlives_timeout_stops_the_sandbox(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    session = active_session(tmp_path)
    session.command_timeout = True
    session.save()
    run_for(monkeypatch, 60, CommandResult("call-1", None, "", timed_out=True), [])

    result = DockerRunnerService().execute(
        "run-7", CommandRequest("call-1", ("sleep", "999"), timeout_seconds=45)
    )

    assert result.timed_out is True
    assert RunnerSession.objects.get().status == RunnerSession.Status.STOPPED


@pytest.mark.django_db
def test_without_timeout_in_the_image_a_late_command_stops_the_sandbox(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    active_session(tmp_path)
    commands: list[list[str]] = []
    run_for(
        monkeypatch, 45, CommandResult("call-1", None, "", timed_out=True), commands
    )

    DockerRunnerService().execute(
        "run-7", CommandRequest("call-1", ("sleep", "999"), timeout_seconds=45)
    )

    assert commands[0][5:] == ["sleep", "999", "runner-timeout=45"]
    assert RunnerSession.objects.get().status == RunnerSession.Status.STOPPED


@pytest.mark.django_db
def test_command_arguments_never_reach_docker_or_timeout_as_options(
    settings, monkeypatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    settings.RUNNER_COMMAND_TIMEOUT_SECONDS = 120
    session = active_session(tmp_path)
    session.command_timeout = True
    session.save()
    commands: list[list[str]] = []
    run_for(monkeypatch, 1, CommandResult("call-1", 0, ""), commands)
    argv = ("--privileged", "-k", "0", "--signal=KILL", "999")

    DockerRunnerService().execute(
        "run-7", CommandRequest("call-1", argv, timeout_seconds=900)
    )

    # Server-owned: the container, timeout's options, and a limit capped by
    # the runner. The request's own arguments follow the command they run.
    assert commands[0][:9] == [
        "docker",
        "exec",
        "--workdir",
        "/workspace",
        "secondshift-run-7",
        "timeout",
        "-k",
        "5",
        "120",
    ]
    assert commands[0][9:-1] == list(argv)


@pytest.mark.parametrize(("code", "available"), [(0, True), (126, False)])
def test_the_runner_checks_the_image_for_timeout(
    monkeypatch, code: int, available: bool
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], timeout: int = 30) -> Mock:
        calls.append(command)
        return completed(code)

    monkeypatch.setattr(DockerRunnerService, "run_command", staticmethod(run))

    assert DockerRunnerService().has_timeout("secondshift-run-7") is available
    assert calls == [["docker", "exec", "secondshift-run-7", "timeout", "1", "true"]]
