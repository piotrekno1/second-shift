"""How the image starts each CLI: only the broker's tools, nothing else."""

import json
import tomllib
from pathlib import Path
from typing import Any, get_args

import pytest

from secondshift.agent.image import agent, outcomes
from secondshift.agent.image.claude import command as claude
from secondshift.agent.image.claude.subagent_hook import WORKER
from secondshift.agent.image.codex import command as codex
from secondshift.runner_protocol.agents import AgentBackend
from secondshift.runner_protocol.schemas import AgentInput

TOKEN = "sk-ant-oat01-test-subscription-token"


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

    argv, env = claude.start(BROKER)
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

    argv, env = codex.start(BROKER)
    assert "--skip-git-repo-check" in argv
    assert env == {
        "CODEX_HOME": str(tmp_path / "codex"),
        "SECONDSHIFT_BROKER_TOKEN": "broker-secret",
    }
    config = (tmp_path / "codex" / "config.toml").read_text()
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
    # The model's own multi-agent setting overrides features.multi_agent.
    assert parsed["agents"]["enabled"] is False
    # The egress proxy refuses Codex's metrics host.
    assert parsed["otel"]["metrics_exporter"] == "none"
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


def test_a_login_check_has_no_tools_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outcomes, "TMP", tmp_path)
    monkeypatch.setattr(outcomes, "AUTH", tmp_path)
    (tmp_path / "claude-token").write_text(TOKEN)
    check = {"max_turns": 1}

    argv, env = claude.start(check)
    assert "--strict-mcp-config" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--mcp-config" not in argv and "--allowedTools" not in argv
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    assert not (tmp_path / "mcp.json").exists()

    argv, env = codex.start(check)
    assert env == {"CODEX_HOME": str(tmp_path / "codex")}
    parsed = tomllib.loads((tmp_path / "codex" / "config.toml").read_text())
    assert "mcp_servers" not in parsed
    assert parsed["features"]["shell_tool"] is False


def test_the_image_and_the_runner_name_the_same_cli_backends() -> None:
    cli_backends = {backend for backend in AgentBackend if not backend.uses_model_api}
    accepted = set(get_args(AgentInput.model_fields["backend"].annotation))

    assert set(agent.BACKENDS) == set(outcomes.AUTH_FILES) == accepted == cli_backends


@pytest.fixture
def container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(outcomes, "TMP", tmp_path)
    monkeypatch.setattr(outcomes, "AUTH", tmp_path)
    (tmp_path / "claude-token").write_text(TOKEN)
    return tmp_path


def option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_a_run_with_subagents_gets_only_the_server_owned_worker(
    container: Path,
) -> None:
    argv, _ = claude.start(BROKER | {"subagents": 2})

    assert option(argv, "--tools") == "Task"
    # The worker is the only sub-agent type, and it has only the broker's tools.
    assert json.loads(option(argv, "--agents")) == {
        WORKER: {
            "description": "Does one task with this run's tools, starting fresh.",
            "prompt": claude.WORKER_PROMPT,
            "tools": ["mcp__secondshift__*"],
        }
    }
    hooks = json.loads(Path(option(argv, "--settings")).read_text())["hooks"]
    assert list(hooks) == ["PreToolUse"]
    [entry] = hooks["PreToolUse"]
    assert entry["matcher"] == "Task|Agent"
    command = entry["hooks"][0]["command"]
    assert str(claude.HOOK) in command and command.endswith(
        f" 2 {container / 'subagents'}"
    )
    # Repository settings and agents stay off.
    assert option(argv, "--setting-sources") == ""
    assert "broker-secret" not in str(argv)


@pytest.mark.parametrize(
    "config", [BROKER, BROKER | {"subagents": 0}, {"subagents": 3}]
)
def test_without_subagents_claude_has_no_tools_of_its_own(
    container: Path, config: dict[str, Any]
) -> None:
    # A login check has no broker, so it never gets sub-agents.
    argv, _ = claude.start(config | {"max_turns": 1})

    assert option(argv, "--tools") == ""
    assert "--agents" not in argv and "--settings" not in argv


def test_each_codex_run_has_its_own_home_sharing_only_the_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_tmp, login = tmp_path / "tmp", tmp_path / "auth"
    run_tmp.mkdir()
    login.mkdir()
    monkeypatch.setattr(outcomes, "TMP", run_tmp)
    monkeypatch.setattr(outcomes, "AUTH", login)
    (login / "auth.json").write_text("{}")

    _, env = codex.start(BROKER)

    home = Path(env["CODEX_HOME"])
    assert home.parent == run_tmp
    # A refresh Codex writes through the link lands in the login's folder.
    (home / "auth.json").write_text('{"refreshed": true}')
    assert (login / "auth.json").read_text() == '{"refreshed": true}'
    # Its config and anything else it writes stay with this run.
    assert sorted(p.name for p in login.iterdir()) == ["auth.json"]
    # A second start in the same container keeps the link.
    codex.start(BROKER)
    assert (home / "auth.json").is_symlink()


@pytest.mark.parametrize("cli", [claude, codex])
def test_the_chosen_model_reaches_each_cli_as_one_option(
    container: Path, cli: Any
) -> None:
    argv, _ = cli.start({**BROKER, "model": "opus"})
    assert option(argv, "--model") == "opus"
    assert argv.count("--model") == 1

    argv, _ = cli.start(BROKER)
    assert "--model" not in argv

    # The runner refuses such names. The image refuses them again.
    with pytest.raises(ValueError):
        cli.start({**BROKER, "model": "--dangerously-skip-permissions"})
