import json
import tomllib
from pathlib import Path

import pytest

from secondshift.agent.image import outcomes
from secondshift.agent.image.claude import command as claude
from secondshift.agent.image.codex import command as codex
from secondshift.agent.image.outcomes import fill_template

# Tries to close the string and add a setting after it.
ESCAPING_URL = 'http://gateway/mcp"\napproval_policy = "on-request'
BROKER = {
    "backend": "codex",
    "prompt": "Audit",
    "max_turns": 3,
    "token_budget": 100,
    "broker_url": ESCAPING_URL,
    "broker_token": "token",
}


@pytest.fixture(autouse=True)
def container_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outcomes, "TMP", tmp_path)
    monkeypatch.setattr(outcomes, "AUTH", tmp_path)
    (tmp_path / "claude-token").write_text("secret")


def test_a_broker_url_cannot_add_codex_settings(tmp_path: Path) -> None:
    codex.start(BROKER)

    config = tomllib.loads((tmp_path / "codex" / "config.toml").read_text())
    assert config["approval_policy"] == "never"
    assert config["mcp_servers"]["secondshift"]["url"] == ESCAPING_URL


def test_a_broker_url_cannot_add_claude_settings(tmp_path: Path) -> None:
    claude.start(BROKER | {"backend": "claude_code"})

    servers = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]
    assert list(servers) == [claude.SERVER]
    assert servers[claude.SERVER]["url"] == ESCAPING_URL


def test_a_template_that_does_not_parse_fails(tmp_path: Path) -> None:
    template = tmp_path / "broken.toml"
    template.write_text("url = $url $url\n")

    with pytest.raises(tomllib.TOMLDecodeError):
        fill_template(template, url="http://gateway/")


def test_a_missing_value_fails_instead_of_leaving_a_placeholder() -> None:
    with pytest.raises(KeyError):
        fill_template(Path(codex.HERE / "broker.toml"), url="http://gateway/")
