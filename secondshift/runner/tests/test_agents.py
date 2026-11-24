"""How the runner starts and checks agent containers."""

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from secondshift.agent.image.claude.subagent_hook import WORKER
from secondshift.core.launcher import services
from secondshift.docker.commands import DockerError
from secondshift.runner import agents
from secondshift.runner.agent_credentials import (
    decrypt_agent_auth,
    provisioning_public_key,
)
from secondshift.runner.agents import agent_command, require_agent_configuration
from secondshift.runner.egress import AgentEgress
from secondshift.runner.models import RunnerSession
from secondshift.runner.services import DockerRunnerService, sandbox_flags
from secondshift.runner_protocol.credentials import encrypt_agent_auth
from secondshift.runner_protocol.schemas import AgentCheckInput, AgentInput
from secondshift.runner_protocol.sessions import RunnerProtocolError

TOKEN = "sk-ant-oat01-test-subscription-token"


IMAGE = "example/ss-agent@sha256:" + "a" * 64
EGRESS = AgentEgress("172.30.0.3")


@pytest.fixture(autouse=True)
def proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs here find the proxy without Docker. test_egress checks the real one."""
    monkeypatch.setattr(agents, "prepare_egress", lambda image: EGRESS)


def test_agent_container_configuration_is_server_owned(
    settings: Any, tmp_path: Path
) -> None:
    settings.AGENT_IMAGE = IMAGE
    settings.AGENT_NETWORK = "ss-agent-egress"
    settings.RUNNER_RUNTIME = "runsc"
    command = agent_command("run-1", tmp_path / "auth", IMAGE, EGRESS)
    assert command[-3:] == [IMAGE, "-m", "secondshift.agent.image.agent"]
    assert "--network=ss-agent-egress" in command
    assert "--env=HTTPS_PROXY=http://172.30.0.3:3128" in command
    assert not any(arg.startswith("--add-host") for arg in command)
    assert "--runtime" in command and "runsc" in command
    assert "--read-only" in command and "--cap-drop=ALL" in command
    assert not any("/workspace" in arg or "docker.sock" in arg for arg in command)
    assert TOKEN not in str(command)
    values = {
        "backend": "claude_code",
        "credential_key": "a" * 32,
        "auth": TOKEN,
        "broker_token": "broker",
        "prompt": "read skill",
        "max_turns": 5,
        "token_budget": 1000,
        "timeout_seconds": 60,
    }
    assert TOKEN not in repr(AgentInput(**values))
    assert "auth" not in AgentInput(**values).model_dump()
    for option in (
        "image",
        "mounts",
        "network",
        "runtime",
        "argv",
        "broker_url",
        "resolv_conf",
        "proxy",
        "proxy_address",
        "env",
    ):
        with pytest.raises(ValidationError):
            AgentInput(**values, **{option: "evil"})


def test_agent_runtime_and_image_fail_closed(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.AGENT_IMAGE = "example/agent:latest"
    settings.AGENT_BROKER_URL = "http://gateway/mcp"
    verify = Mock()
    resolve = Mock(return_value="sha256:" + "b" * 64)
    monkeypatch.setattr(
        "secondshift.runner.agents.DockerRunnerService.verify_runtime", verify
    )
    monkeypatch.setattr(agents, "resolve_image_id", resolve)
    with pytest.raises(RunnerProtocolError, match="digest"):
        require_agent_configuration()
    verify.assert_not_called()
    resolve.assert_not_called()


def test_local_runner_runs_the_exact_image_behind_its_tag(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.AGENT_IMAGE = "secondshift-agent:local"
    settings.AGENT_BROKER_URL = "http://gateway/mcp"
    settings.AGENT_IMAGE_TAGS_ALLOWED = True
    image_id = "sha256:" + "b" * 64
    monkeypatch.setattr(
        "secondshift.runner.agents.DockerRunnerService.verify_runtime", Mock()
    )
    monkeypatch.setattr(agents, "resolve_image_id", Mock(return_value=image_id))
    assert require_agent_configuration() == image_id

    monkeypatch.setattr(
        agents,
        "resolve_image_id",
        Mock(side_effect=DockerError("docker image failed.")),
    )
    with pytest.raises(RunnerProtocolError, match="isn't built"):
        require_agent_configuration()


def test_local_content_address_is_a_pinned_agent_image(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.AGENT_IMAGE = "sha256:" + "a" * 64
    settings.AGENT_BROKER_URL = "http://gateway/mcp"
    verify = Mock()
    monkeypatch.setattr(
        "secondshift.runner.agents.DockerRunnerService.verify_runtime", verify
    )
    assert require_agent_configuration() == settings.AGENT_IMAGE
    verify.assert_called_once()


def test_agent_provisioning_encrypts_credentials_for_only_its_runner(
    settings: Any, tmp_path: Path
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "auth"
    envelope = encrypt_agent_auth(provisioning_public_key(), TOKEN)
    assert TOKEN not in envelope
    assert decrypt_agent_auth(envelope) == TOKEN
    settings.AGENT_AUTH_ROOT = tmp_path / "other-runner"
    with pytest.raises(RunnerProtocolError, match="encrypted"):
        decrypt_agent_auth(envelope)


@pytest.mark.django_db
def test_runner_cleans_agent_container_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.AGENT_IMAGE = IMAGE
    settings.AGENT_BROKER_URL = "http://gateway:8001/gateway/agent/mcp"
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    RunnerSession.objects.create(
        session_id="run-1",
        run_id=1,
        repository="example/agents",
        commit_sha="a" * 40,
        image="test",
        container_name="sandbox",
        workspace_path=str(tmp_path / "workspace"),
        status="active",
    )
    monkeypatch.setattr(agents, "require_agent_configuration", lambda: IMAGE)
    remove = Mock()
    monkeypatch.setattr(agents.DockerRunnerService, "remove_container", remove)

    def timeout(command: list[str], **kwargs: Any) -> None:
        assert TOKEN not in str(command)
        assert TOKEN not in kwargs["input"].decode()
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(agents.subprocess, "run", timeout)
    request = AgentInput(
        backend="claude_code",
        credential_key="a" * 32,
        auth=encrypt_agent_auth(provisioning_public_key(), TOKEN),
        broker_token="broker",
        prompt="Read skill",
        max_turns=5,
        token_budget=1000,
        timeout_seconds=1,
    )
    result = agents.execute_agent("run-1", request)
    assert result.reason == "timed_out"
    remove.assert_called_once_with("secondshift-agent-run-1")
    with pytest.raises(RunnerProtocolError, match="unknown"):
        agents.execute_agent("run-1", request)


def test_launcher_passes_agent_settings_but_no_control_plane_keys(
    settings: Any,
) -> None:
    settings.AGENT_IMAGE = IMAGE
    parent = {
        "SECONDSHIFT_AGENT_IMAGE": IMAGE,
        "SECONDSHIFT_AGENT_BROKER_URL": "http://gateway/mcp",
        "SECONDSHIFT_ENCRYPTION_KEY": "private-key",
    }
    processes = {
        process.name: process for process in services("127.0.0.1", 8000, parent)
    }
    assert processes["runner"].env["SECONDSHIFT_AGENT_IMAGE"] == IMAGE
    assert "SECONDSHIFT_ENCRYPTION_KEY" not in processes["runner"].env


CHECK = {
    "backend": "claude_code",
    "credential_key": "c" * 32,
}


def test_a_login_check_takes_no_prompt_or_container_options(
    settings: Any, tmp_path: Path
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    auth = encrypt_agent_auth(provisioning_public_key(), TOKEN)
    request = AgentCheckInput(**CHECK, auth=auth)
    assert TOKEN not in repr(request) and "auth" not in request.model_dump()
    for option in ("prompt", "max_turns", "image", "broker_url", "network", "argv"):
        with pytest.raises(ValidationError):
            AgentCheckInput(**CHECK, auth=auth, **{option: "evil"})


def test_runner_checks_a_login_with_a_fixed_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    monkeypatch.setattr(agents, "require_agent_configuration", lambda: IMAGE)
    remove = Mock()
    monkeypatch.setattr(agents.DockerRunnerService, "remove_container", remove)
    seen: dict[str, Any] = {}

    def run(command: list[str], **kwargs: Any) -> Any:
        seen["command"] = command
        seen["config"] = json.loads(kwargs["input"])
        assert kwargs["timeout"] == agents.CHECK_TIMEOUT_SECONDS
        kwargs["stdout"].write(b'{"succeeded": true, "reason": "completed"}')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agents.subprocess, "run", run)
    request = AgentCheckInput(
        **CHECK, auth=encrypt_agent_auth(provisioning_public_key(), TOKEN)
    )
    assert agents.check_agent(request).reason == "completed"
    assert seen["config"] == {
        "backend": "claude_code",
        "prompt": agents.CHECK_PROMPT,
        "max_turns": 1,
        "token_budget": agents.CHECK_TOKEN_BUDGET,
    }
    assert TOKEN not in str(seen["command"])
    (name,) = remove.call_args.args
    assert name.startswith(f"secondshift-agent-check-{'c' * 32}-")


@pytest.mark.parametrize(
    ("exit_code", "output", "reason"),
    [
        # Stopped, or killed for running out of memory.
        (137, b"", "agent_stopped"),
        (
            0,
            b'{"reason": "completed", "succeeded": true, "report": "Done"}',
            "completed",
        ),
        (0, b"not json", "invalid_agent_output"),
    ],
)
def test_a_stopped_agent_container_is_not_blamed_on_the_login(
    monkeypatch: pytest.MonkeyPatch, exit_code: int, output: bytes, reason: str
) -> None:
    monkeypatch.setattr(agents.DockerRunnerService, "remove_container", Mock())

    def run(command: list[str], **kwargs: Any) -> Mock:
        kwargs["stdout"].write(output)
        return Mock(returncode=exit_code)

    monkeypatch.setattr(agents.subprocess, "run", run)

    result = agents.run_agent_container("agent", ["docker"], {}, 10)

    assert result.reason == reason


def test_agent_and_command_sandboxes_get_the_same_hardening(
    settings: Any, tmp_path: Path
) -> None:
    settings.AGENT_IMAGE = IMAGE
    settings.RUNNER_RUNTIME = "runsc"
    agent = agent_command("run-1", tmp_path / "auth", IMAGE, EGRESS)
    sandbox = DockerRunnerService.create_command(
        "secondshift-runner:test", "secondshift-run-1", tmp_path / "ws"
    )

    hardening = sandbox_flags()
    assert f"--user={settings.RUNNER_SANDBOX_UID}:{settings.RUNNER_SANDBOX_GID}" in (
        hardening
    )
    for command in (agent, sandbox):
        start = command.index(hardening[0])
        assert command[start : start + len(hardening)] == hardening


def option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def start(**fields: Any) -> dict[str, Any]:
    task = {
        "subagent_type": WORKER,
        "prompt": "Check it",
        "run_in_background": False,
    } | fields
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": task}


@pytest.mark.django_db
@pytest.mark.parametrize(("backend", "sent"), [("claude_code", 2), ("codex", 0)])
def test_the_runner_passes_subagents_only_to_claude_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
    backend: str,
    sent: int,
) -> None:
    settings.AGENT_BROKER_URL = "http://gateway:8001/gateway/agent/mcp"
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    RunnerSession.objects.create(
        session_id="run-1",
        run_id=1,
        repository="example/agents",
        commit_sha="a" * 40,
        image="test",
        container_name="sandbox",
        workspace_path=str(tmp_path / "workspace"),
        status="active",
    )
    monkeypatch.setattr(agents, "require_agent_configuration", lambda: IMAGE)
    monkeypatch.setattr(
        agents.DockerRunnerService, "reserve_command", Mock(return_value=None)
    )
    launched = Mock(return_value=agents.AgentOutput())
    monkeypatch.setattr(agents, "run_agent_container", launched)

    agents.execute_agent(
        "run-1",
        AgentInput(
            backend=backend,
            credential_key="a" * 32,
            auth=encrypt_agent_auth(provisioning_public_key(), TOKEN),
            broker_token="broker",
            prompt="Read skill",
            max_turns=5,
            token_budget=1000,
            timeout_seconds=60,
            subagents=2,
        ),
    )

    assert launched.call_args.args[2]["subagents"] == sent


# Runs and checks sharing a login


def login_entries(root: Path) -> list[str]:
    """The login's folder, lock or staging copies, beside the runner's key."""
    return sorted(p.name for p in root.iterdir() if "c" * 32 in p.name)


def test_a_login_runs_several_agents_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    monkeypatch.setattr(agents, "require_agent_configuration", lambda: IMAGE)
    monkeypatch.setattr(agents.DockerRunnerService, "remove_container", Mock())
    request = AgentCheckInput(
        **CHECK, auth=encrypt_agent_auth(provisioning_public_key(), TOKEN)
    )
    names: list[str] = []

    def run(command: list[str], **kwargs: Any) -> Any:
        names.append(command[command.index("--name") + 1])
        # A second check starts while the first is still running.
        if len(names) == 1:
            assert agents.check_agent(request).reason == "completed"
        kwargs["stdout"].write(b'{"succeeded": true, "reason": "completed"}')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agents.subprocess, "run", run)

    assert agents.check_agent(request).reason == "completed"
    # Each gets its own container, and both use the login's one folder.
    assert len(set(names)) == 2
    assert login_entries(settings.AGENT_AUTH_ROOT) == ["c" * 32]


def test_a_login_folder_created_meanwhile_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    request = AgentCheckInput(
        **CHECK, auth=encrypt_agent_auth(provisioning_public_key(), TOKEN)
    )
    original = Path.rename

    def another_run_wins(self: Path, target: Path) -> Path:
        # Another run renames its own copy into place first.
        target.mkdir()
        (target / "claude-token").write_text("theirs")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", another_run_wins)

    path = agents.provision_auth(request)

    assert (path / "claude-token").read_text() == "theirs"
    assert login_entries(settings.AGENT_AUTH_ROOT) == ["c" * 32]


def test_removing_a_login_deletes_its_folder_and_old_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    request = AgentCheckInput(
        **CHECK, auth=encrypt_agent_auth(provisioning_public_key(), TOKEN)
    )
    agents.provision_auth(request)
    (settings.AGENT_AUTH_ROOT / f"{'c' * 32}.lock").touch()
    monkeypatch.setattr(
        DockerRunnerService,
        "run_command",
        staticmethod(
            lambda command, timeout=30: subprocess.CompletedProcess(command, 0, "")
        ),
    )

    agents.remove_agent_credentials("c" * 32)

    assert login_entries(settings.AGENT_AUTH_ROOT) == []


def test_a_login_folder_keeps_only_the_login(tmp_path: Path, settings: Any) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    request = AgentCheckInput(
        **CHECK, auth=encrypt_agent_auth(provisioning_public_key(), TOKEN)
    )
    path = agents.provision_auth(request)
    # What older releases let Codex leave here, with the folder as its home.
    (path / "memories_1.sqlite").write_text("an earlier run")
    (path / "shell_snapshots").mkdir()
    (path / "shell_snapshots" / "one.sh").write_text("echo")

    agents.provision_auth(request)

    assert sorted(p.name for p in path.iterdir()) == ["claude-token"]


def test_the_runner_leaves_the_codex_home_to_the_image(
    tmp_path: Path, settings: Any
) -> None:
    command = agent_command("run-7", tmp_path, IMAGE, EGRESS)

    assert not any("CODEX_HOME" in part for part in command)
