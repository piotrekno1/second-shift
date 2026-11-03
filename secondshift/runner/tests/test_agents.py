import json
import subprocess
import tomllib
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
    assert "--add-host=host.docker.internal:host-gateway" in command
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


BROKER = {
    "broker_url": "http://gateway:8001/gateway/agent/mcp",
    "broker_token": "broker-secret",
    "max_turns": 4,
}


def test_packaged_agents_disable_native_tools_and_use_only_the_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "TMP", tmp_path)
    monkeypatch.setattr(agent, "AUTH", tmp_path)
    (tmp_path / "claude-token").write_text(TOKEN)

    argv, env = agent.claude(BROKER)
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    assert TOKEN not in str(argv) and "broker-secret" not in str(argv)
    server = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]
    assert server == {
        "secondshift": {
            "type": "http",
            "url": BROKER["broker_url"],
            "headers": {"Authorization": "Bearer broker-secret"},
        }
    }

    argv, env = agent.codex(BROKER)
    assert "--skip-git-repo-check" in argv
    assert env == {"SECONDSHIFT_BROKER_TOKEN": "broker-secret"}
    config = (tmp_path / "config.toml").read_text()
    parsed = tomllib.loads(config)
    assert parsed["approval_policy"] == "never"
    assert parsed["sandbox_mode"] == "read-only"
    assert parsed["mcp_servers"]["secondshift"]["default_tools_approval_mode"] == (
        "approve"
    )
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
    assert f'url = "{BROKER["broker_url"]}"' in config
    assert 'bearer_token_env_var = "SECONDSHIFT_BROKER_TOKEN"' in config
    assert "broker-secret" not in config


def outcome_of(read: Any, events: list[dict[str, Any]], exit_code: int = 0) -> Any:
    outcome = agent.Outcome()
    for event in events:
        read(event, outcome)
    return agent.result(outcome, exit_code)


def test_agent_events_keep_only_final_report_and_numeric_usage() -> None:
    done = outcome_of(
        agent.read_claude,
        [
            {
                "type": "result",
                "result": "Done",
                "is_error": False,
                "usage": {"input_tokens": 20, "secret": TOKEN},
                "stderr": TOKEN,
            }
        ],
    )
    assert done == {
        "report": "Done",
        "succeeded": True,
        "reason": "completed",
        "usage": {"input_tokens": 20},
    }
    codex_done = outcome_of(
        agent.read_codex,
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi"}},
            {"type": "turn.completed", "usage": {"output_tokens": 3}},
        ],
    )
    assert codex_done["report"] == "Hi" and codex_done["succeeded"] is True
    # A failing CLI exit code never counts as success.
    assert outcome_of(agent.read_codex, [], exit_code=0)["reason"] == "agent_failed"


@pytest.mark.parametrize(
    ("read", "events", "reason"),
    [
        (
            agent.read_claude,
            [
                {
                    "type": "result",
                    "is_error": True,
                    "result": "API Error: 401 Invalid bearer token",
                }
            ],
            "reconnect_required",
        ),
        (
            agent.read_claude,
            [{"type": "result", "subtype": "error_max_turns", "is_error": True}],
            "turn_budget_exhausted",
        ),
        (
            agent.read_codex,
            [{"type": "turn.failed", "error": {"message": "You hit your usage limit"}}],
            "quota_exhausted",
        ),
    ],
)
def test_failed_agents_return_a_reason_but_never_their_error_text(
    read: Any, events: list[dict[str, Any]], reason: str
) -> None:
    failed = outcome_of(read, events)
    assert failed["reason"] == reason
    assert failed["succeeded"] is False
    assert failed["report"] == ""


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
