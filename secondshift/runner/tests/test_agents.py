import json
import subprocess
import sys
import time
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
from secondshift.runner.agent_image import agent, claude, codex, outcomes
from secondshift.runner.agent_protocol import AgentCheckInput, AgentInput
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
    command = agent_command("run-1", tmp_path / "auth", IMAGE)
    assert command[-3:] == [IMAGE, "-m", "secondshift.runner.agent_image.agent"]
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
    for option in (
        "image",
        "mounts",
        "network",
        "runtime",
        "argv",
        "broker_url",
        "resolv_conf",
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
        Mock(side_effect=subprocess.CalledProcessError(1, [])),
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


BROKER = {
    "broker_url": "http://gateway:8001/gateway/agent/mcp",
    "broker_token": "broker-secret",
    "max_turns": 4,
}


def test_packaged_agents_disable_native_tools_and_use_only_the_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outcomes, "TMP", tmp_path)
    monkeypatch.setattr(outcomes, "AUTH", tmp_path)
    (tmp_path / "claude-token").write_text(TOKEN)

    argv, env = claude.claude(BROKER)
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

    argv, env = codex.codex(BROKER)
    assert "--skip-git-repo-check" in argv
    assert env == {"SECONDSHIFT_BROKER_TOKEN": "broker-secret"}
    config = (tmp_path / "config.toml").read_text()
    parsed = tomllib.loads(config)
    assert parsed["approval_policy"] == "never"
    assert parsed["sandbox_mode"] == "read-only"
    assert parsed["mcp_servers"]["secondshift"]["default_tools_approval_mode"] == (
        "approve"
    )
    # A slow broker must not start the turn without tools, or the model reports
    # the tools as missing and the run still succeeds.
    assert parsed["mcp_servers"]["secondshift"]["required"] is True
    assert parsed["mcp_servers"]["secondshift"]["startup_timeout_sec"] == (
        codex.BROKER_STARTUP_TIMEOUT_SECONDS
    )
    assert parsed["features"]["incremental_tools"] is False
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
    outcome = outcomes.Outcome()
    for event in events:
        read(event, outcome)
    return agent.result(outcome, exit_code)


def test_agent_events_keep_only_final_report_and_numeric_usage() -> None:
    done = outcome_of(
        claude.read_claude,
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
        codex.read_codex,
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi"}},
            {"type": "turn.completed", "usage": {"output_tokens": 3}},
        ],
    )
    assert codex_done["report"] == "Hi" and codex_done["succeeded"] is True
    # A failing CLI exit code never counts as success.
    assert outcome_of(codex.read_codex, [], exit_code=0)["reason"] == "agent_failed"


def reply(message_id: str, sub_agent: bool = False, **usage: int) -> dict[str, Any]:
    return {
        "type": "assistant",
        "parent_tool_use_id": "toolu_1" if sub_agent else None,
        "message": {"id": message_id, "usage": usage},
    }


def test_claude_usage_adds_up_each_reply_once_with_sub_agents() -> None:
    outcome = outcomes.Outcome()
    events = [
        # One reply can arrive as several events with the same usage.
        reply("msg_1", input_tokens=2, cache_read_input_tokens=5000),
        reply("msg_1", input_tokens=2, cache_read_input_tokens=5000),
        reply("msg_2", sub_agent=True, input_tokens=2, cache_read_input_tokens=1000),
        reply("msg_3", sub_agent=True, input_tokens=2, output_tokens=16),
    ]
    for event in events:
        claude.read_claude(event, outcome)

    # Counted as replies arrive, so a budget can stop the run before it ends.
    assert outcome.usage == {
        "input_tokens": 6,
        "cache_read_input_tokens": 6000,
        "output_tokens": 16,
    }

    # The final usage leaves out sub-agents. The per-model usage includes them.
    # The largest of each count is kept.
    claude.read_claude(
        {
            "type": "result",
            "result": "Done",
            "is_error": False,
            "usage": {"input_tokens": 4, "output_tokens": 900},
            "modelUsage": {
                "claude-haiku": {"outputTokens": 1500, "costUSD": 0.01},
                "claude-sonnet": {"inputTokens": 3, "cacheReadInputTokens": 7000},
            },
        },
        outcome,
    )
    assert outcome.usage == {
        "input_tokens": 6,
        "cache_read_input_tokens": 7000,
        "output_tokens": 1500,
    }


def test_the_token_budget_stops_claude_mid_run(monkeypatch) -> None:
    """A CLI that keeps going past the budget is stopped, not waited for."""
    events = [
        reply("msg_1", cache_read_input_tokens=600),
        reply("msg_2", sub_agent=True, cache_read_input_tokens=600),
    ]
    script = (
        "import json, sys, time\n"
        f"for event in {events!r}:\n"
        "    print(json.dumps(event), flush=True)\n"
        "time.sleep(60)\n"
    )

    def start(config: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
        return [sys.executable, "-c", script], {}

    monkeypatch.setitem(
        agent.BACKENDS, "claude_code", (sys.executable, start, claude.read_claude)
    )
    started = time.monotonic()

    finished = agent.run(
        {"backend": "claude_code", "prompt": "Audit", "token_budget": 1000}
    )

    assert time.monotonic() - started < 30
    assert finished["reason"] == "token_budget_exhausted"
    assert finished["succeeded"] is False
    assert finished["usage"] == {"cache_read_input_tokens": 1200}


@pytest.mark.parametrize(
    ("read", "events", "reason"),
    [
        (
            claude.read_claude,
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
            claude.read_claude,
            [{"type": "result", "subtype": "error_max_turns", "is_error": True}],
            "turn_budget_exhausted",
        ),
        (
            codex.read_codex,
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


def test_a_login_check_has_no_tools_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outcomes, "TMP", tmp_path)
    monkeypatch.setattr(outcomes, "AUTH", tmp_path)
    (tmp_path / "claude-token").write_text(TOKEN)
    check = {"max_turns": 1}

    argv, env = claude.claude(check)
    assert "--strict-mcp-config" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--mcp-config" not in argv and "--allowedTools" not in argv
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    assert not (tmp_path / "mcp.json").exists()

    argv, env = codex.codex(check)
    assert env == {}
    parsed = tomllib.loads((tmp_path / "config.toml").read_text())
    assert "mcp_servers" not in parsed
    assert parsed["features"]["shell_tool"] is False


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
    remove.assert_called_once_with(f"secondshift-agent-check-{'c' * 32}")


def test_agent_resolver_mount_is_server_owned_and_read_only(
    settings: Any, tmp_path: Path
) -> None:
    resolver = tmp_path / "resolv.conf"
    resolver.write_text("nameserver 1.1.1.1\n")
    settings.AGENT_RESOLV_CONF = str(resolver)
    command = agent_command("run-1", tmp_path / "auth", IMAGE)
    assert f"--mount=type=bind,src={resolver},dst=/etc/resolv.conf,readonly" in command
    for value in ("relative.conf", str(tmp_path / "missing")):
        settings.AGENT_RESOLV_CONF = value
        with pytest.raises(RunnerProtocolError, match="resolver"):
            agent_command("run-1", tmp_path / "auth", IMAGE)


def test_agent_resolver_refuses_symlinks(settings: Any, tmp_path: Path) -> None:
    resolver = tmp_path / "resolv.conf"
    resolver.write_text("nameserver 1.1.1.1\n")
    alias = tmp_path / "alias.conf"
    alias.symlink_to(resolver)
    settings.AGENT_RESOLV_CONF = str(alias)
    with pytest.raises(RunnerProtocolError, match="resolver"):
        agent_command("run-1", tmp_path / "auth", IMAGE)
