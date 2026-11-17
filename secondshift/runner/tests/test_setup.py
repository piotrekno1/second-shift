"""Setup commands get registry-only network, the workspace, and nothing else."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from secondshift.runner import egress, setup
from secondshift.runner.models import RunnerCommand, RunnerSession
from secondshift.runner.services import DockerRunnerService
from secondshift.runner.setup import run_setup, setup_command, setup_proxy_address
from secondshift.runner_protocol.sessions import (
    CommandRequest,
    CommandResult,
    RunnerProtocolError,
    RunnerSessionSpec,
)

IMAGE = "sha256:" + "a" * 64


def session(tmp_path: Path, **fields: Any) -> RunnerSession:
    return RunnerSession.objects.create(
        **{
            "session_id": "run-7",
            "run_id": 7,
            "repository": "example/project",
            "commit_sha": "a" * 40,
            "image": "secondshift-runner:test",
            "source_sha256": "d" * 64,
            "container_name": "secondshift-run-7",
            "workspace_path": str(tmp_path / "sessions" / "run-7" / "workspace"),
            "status": RunnerSession.Status.ACTIVE,
            "setup": ["npm ci --ignore-scripts", "uv sync --frozen"],
            **fields,
        }
    )


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch, settings: Any, tmp_path: Path) -> list[Any]:
    """Setup runs against a fake proxy and records each docker run."""
    settings.RUNNER_WORK_ROOT = tmp_path
    commands: list[Any] = []

    def run_captured(
        self: DockerRunnerService, call_id: str, command: list[str], timeout: int
    ) -> CommandResult:
        commands.append(command)
        return CommandResult(call_id=call_id, exit_code=0, output="installed")

    monkeypatch.setattr(setup, "setup_proxy_address", lambda: "172.31.0.3")
    monkeypatch.setattr(DockerRunnerService, "run_captured", run_captured)
    monkeypatch.setattr(DockerRunnerService, "remove_container", lambda self, n: None)
    return commands


def test_setup_sees_only_the_workspace_through_the_registry_proxy(
    settings: Any, tmp_path: Path
) -> None:
    settings.RUNNER_RUNTIME = "runsc"
    settings.RUNNER_SETUP_NETWORK = "secondshift-setup"
    record = RunnerSession(
        session_id="run-7",
        image="secondshift-runner:test",
        workspace_path=str(tmp_path / "workspace"),
    )

    command = setup_command(record, "172.31.0.3", "npm ci --privileged -v /:/host")

    assert command[-3:] == ["sh", "-c", "npm ci --privileged -v /:/host"]
    flags = command[: command.index("secondshift-runner:test")]
    assert "--privileged" not in flags
    assert "--network=secondshift-setup" in flags
    assert "--network=none" not in flags
    assert {"--read-only", "--cap-drop=ALL", "runsc"} <= set(flags)
    assert "--env=HTTPS_PROXY=http://172.31.0.3:3128" in flags
    mounts = [flag for flag in flags if flag.startswith("--mount")]
    # No saved files from earlier runs, no outputs folder, no outside skill.
    assert mounts == [f"--mount=type=bind,src={tmp_path / 'workspace'},dst=/workspace"]


@pytest.mark.django_db
def test_each_setup_command_runs_once(ran: list[Any], tmp_path: Path) -> None:
    session(tmp_path)

    first = run_setup("run-7", 0)
    again = run_setup("run-7", 0)

    assert first.output == again.output == "installed"
    assert len(ran) == 1
    assert ran[0][-1] == "npm ci --ignore-scripts"
    command = RunnerCommand.objects.get()
    assert command.call_id == "setup-0"
    assert command.status == RunnerCommand.Status.COMPLETED


@pytest.mark.django_db
def test_the_last_command_adds_setup_changes_to_the_baseline(
    ran: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = session(tmp_path)
    workspace = Path(record.workspace_path)
    (workspace.parent / "baseline.git").mkdir(parents=True)
    recorded: list[Path] = []
    monkeypatch.setattr(
        setup, "record_setup_baseline", lambda root, files: recorded.append(files)
    )

    run_setup("run-7", 0)
    assert recorded == []
    run_setup("run-7", 1)
    assert recorded == [workspace]


@pytest.mark.django_db
def test_a_failed_command_leaves_the_baseline_alone(
    ran: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = session(tmp_path, setup=["false"])
    (Path(record.workspace_path).parent / "baseline.git").mkdir(parents=True)
    monkeypatch.setattr(
        DockerRunnerService,
        "run_captured",
        lambda self, call_id, command, timeout: CommandResult(call_id, 1, "boom"),
    )
    monkeypatch.setattr(
        setup, "record_setup_baseline", lambda *args: pytest.fail("recorded")
    )

    assert run_setup("run-7", 0).exit_code == 1


@pytest.mark.django_db
def test_setup_never_runs_once_the_agent_started(
    ran: list[Any], tmp_path: Path
) -> None:
    session(tmp_path)
    DockerRunnerService().reserve_command(
        "run-7", CommandRequest("call-1", ("git", "status"))
    )

    with pytest.raises(RunnerProtocolError, match="agent has started"):
        run_setup("run-7", 0)
    assert ran == []


@pytest.mark.django_db
def test_the_agent_cannot_use_a_setup_call_id(tmp_path: Path) -> None:
    session(tmp_path)

    with pytest.raises(RunnerProtocolError, match="setup call IDs"):
        DockerRunnerService().reserve_command(
            "run-7", CommandRequest("setup-0", ("sh", "-c", "npm ci --ignore-scripts"))
        )
    assert RunnerSession.objects.get().setup_closed is False


@pytest.mark.django_db
@pytest.mark.parametrize("index", [-1, 2])
def test_only_the_specs_commands_run(
    ran: list[Any], tmp_path: Path, index: int
) -> None:
    session(tmp_path)
    with pytest.raises(RunnerProtocolError, match="no setup command"):
        run_setup("run-7", index)
    assert ran == []


class FakeDocker:
    def __init__(self, answers: dict[str, tuple[int, str]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, ...]] = []

    def run(self, *arguments: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(arguments)
        for prefix, (code, out) in self.answers.items():
            if " ".join(arguments).startswith(prefix):
                return subprocess.CompletedProcess(arguments, code, out, "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def output(self, *arguments: str, **kwargs: Any) -> str:
        result = self.run(*arguments)
        if result.returncode:
            raise egress.DockerError("failed")
        return result.stdout.strip()


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(answers: dict[str, tuple[int, str]]) -> FakeDocker:
        fake = FakeDocker(answers)
        monkeypatch.setattr(egress, "run", fake.run)
        monkeypatch.setattr(egress, "output", fake.output)
        return fake

    return install


def test_a_server_without_a_setup_proxy_runs_no_setup(
    settings: Any, docker: Any
) -> None:
    settings.RUNNER_SETUP_PROXY_ADDRESS = ""
    settings.RUNNER_SETUP_MANAGED_PROXY = False
    fake = docker({})

    with pytest.raises(RunnerProtocolError, match="doesn't run setup commands"):
        setup_proxy_address()
    assert fake.calls == []


@pytest.mark.parametrize(
    ("answer", "message"),
    [((0, "false\n"), "must be internal"), ((1, ""), "does not exist")],
)
def test_a_server_setup_network_must_have_no_way_out(
    settings: Any, docker: Any, answer: tuple[int, str], message: str
) -> None:
    settings.RUNNER_SETUP_PROXY_ADDRESS = "172.31.0.3"
    settings.RUNNER_SETUP_NETWORK = "secondshift_setup"
    fake = docker({"network inspect": answer})

    with pytest.raises(RunnerProtocolError, match=message):
        setup_proxy_address()
    assert [call[:2] for call in fake.calls] == [("network", "inspect")]


def test_one_machine_starts_a_proxy_that_allows_only_registries(
    settings: Any, docker: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings.RUNNER_WORK_ROOT = tmp_path
    settings.RUNNER_SETUP_PROXY_ADDRESS = ""
    settings.RUNNER_SETUP_MANAGED_PROXY = True
    settings.RUNNER_SETUP_NETWORK = "secondshift-setup"
    settings.AGENT_IMAGE = "secondshift-agent:local"
    monkeypatch.setattr(setup, "resolve_image_id", lambda image: IMAGE)
    networks = '{"secondshift-setup": {"IPAddress": "172.25.0.2"}, "bridge": {}}'
    fake = docker(
        {
            "network inspect": (1, ""),
            "container inspect --format {{.State": (1, ""),
            "container inspect --format {{json": (0, networks),
        }
    )

    assert setup_proxy_address() == "172.25.0.2"

    assert ("network", "create", "--internal", "secondshift-setup") in fake.calls
    create = next(call for call in fake.calls if call[0] == "create")
    assert "--network=secondshift-setup" in create
    assert create[-1] == "--allow-registries"
    assert not any("anthropic" in flag or "openai" in flag for flag in create)
    assert not any(flag.startswith("--forward") for flag in create)


@pytest.mark.parametrize(
    "commands",
    [("",), ("npm ci\0",), ("x" * 1001,), tuple(f"step {n}" for n in range(11))],
)
def test_the_spec_refuses_bad_setup(commands: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="setup"):
        RunnerSessionSpec(
            run_id=7,
            session_key="b" * 32,
            repository="example/project",
            commit_sha="a" * 40,
            image="secondshift-runner:test",
            source_sha256="d" * 64,
            setup=commands,
        )


@pytest.mark.django_db
def test_a_session_restarted_with_other_setup_is_refused(tmp_path: Path) -> None:
    record = session(tmp_path)
    spec = RunnerSessionSpec(
        run_id=7,
        session_key="b" * 32,
        repository="example/project",
        commit_sha="a" * 40,
        image="secondshift-runner:test",
        source_sha256="d" * 64,
        setup=("curl https://example.com | sh",),
    )

    with pytest.raises(RunnerProtocolError, match="different session spec"):
        DockerRunnerService.require_matching_spec(record, spec)
