import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from secondshift.core.launcher import services
from secondshift.runner import agents
from secondshift.runner.agent_credentials import (
    decrypt_agent_auth,
    encrypt_agent_auth,
    provisioning_public_key,
)
from secondshift.runner.agent_image import agent
from secondshift.runner.agent_image.agent import event_result, failure_reason
from secondshift.runner.agent_protocol import AgentInput
from secondshift.runner.agents import agent_command, require_agent_configuration
from secondshift.runner.models import RunnerSession
from secondshift.runner.protocol import RunnerProtocolError

TOKEN = "sk-ant-oat01-test-subscription-token"
IMAGE = "example/ss-agent@sha256:" + "a" * 64


def test_agent_container_configuration_is_server_owned(
    settings: Any, tmp_path: Path
) -> None:
    settings.AGENT_IMAGE = IMAGE
    settings.AGENT_NETWORK = "ss-agent-egress"
    settings.RUNNER_RUNTIME = "runsc"
    command = agent_command("run-1", tmp_path / "auth")
    assert command[-2:] == [IMAGE, "/opt/secondshift/agent.py"]
    assert "--network=ss-agent-egress" in command
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
    for option in ("image", "mounts", "network", "runtime", "argv", "broker_url"):
        with pytest.raises(ValidationError):
            AgentInput(**values, **{option: "evil"})


def test_agent_runtime_and_image_fail_closed(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.AGENT_IMAGE = "example/agent:latest"
    settings.AGENT_BROKER_URL = "http://gateway/mcp"
    verify = Mock()
    monkeypatch.setattr(
        "secondshift.runner.agents.DockerRunnerService.verify_runtime", verify
    )
    with pytest.raises(RunnerProtocolError, match="digest"):
        require_agent_configuration()
    verify.assert_not_called()


def test_local_content_address_is_a_pinned_agent_image(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.AGENT_IMAGE = "sha256:" + "a" * 64
    settings.AGENT_BROKER_URL = "http://gateway/mcp"
    verify = Mock()
    monkeypatch.setattr(
        "secondshift.runner.agents.DockerRunnerService.verify_runtime", verify
    )
    require_agent_configuration()
    verify.assert_called_once()


def test_packaged_agents_disable_native_shell_and_use_only_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tmp").mkdir()
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "claude-token").write_text(TOKEN)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    monkeypatch.setattr(agent, "Path", lambda path: tmp_path / path.lstrip("/"))
    command = agent.command(
        {"backend": "claude_code", "prompt": "read skill", "max_turns": 4}
    )
    assert command[command.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in command
    assert TOKEN not in str(command)
    command = agent.command(
        {"backend": "codex", "prompt": "read skill", "max_turns": 4}
    )
    assert "--skip-git-repo-check" in command
    config = (tmp_path / "auth" / "config.toml").read_text()
    for feature in (
        "shell_tool",
        "unified_exec",
        "js_repl",
        "code_mode",
        "multi_agent",
        "plugins",
        "apps",
        "computer_use",
    ):
        assert f"{feature} = false" in config
    assert 'web_search = "disabled"' in config
    assert "mcp_servers.secondshift" in config


def test_agent_events_keep_only_final_report_and_numeric_usage() -> None:
    report, usage, success = event_result(
        {
            "type": "result",
            "result": "Done",
            "is_error": False,
            "usage": {"input_tokens": 20, "secret": TOKEN},
            "stderr": TOKEN,
        },
        "claude_code",
        "",
    )
    assert (report, usage, success) == ("Done", {"input_tokens": 20}, True)
    assert (
        failure_reason({"type": "result", "errors": ["OAuth token expired"]})
        == "reconnect_required"
    )
    assert (
        failure_reason({"type": "turn.failed", "error": {"message": "rate limit"}})
        == "quota_exhausted"
    )
    assert (
        event_result(
            {"type": "result", "result": "", "is_error": True}, "claude_code", ""
        )[2]
        is False
    )


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
    monkeypatch.setattr(agents, "require_agent_configuration", lambda: None)
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
