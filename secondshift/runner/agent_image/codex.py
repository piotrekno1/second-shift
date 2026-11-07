"""Codex: how to start it, and how to read its JSON events."""

import json
from typing import Any

from secondshift.runner.agent_image import outcomes
from secondshift.runner.agent_image.outcomes import (
    MAX_REPORT_CHARS,
    Outcome,
    numbers,
    private_file,
)

BROKER_TOKEN_VAR = "SECONDSHIFT_BROKER_TOKEN"
BROKER_STARTUP_TIMEOUT_SECONDS = 60


def codex(config: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    """Codex with no built-in tools and only the run's broker.

    A login check has no broker, so it has no tools at all.
    """
    broker = ""
    if config.get("broker_url"):
        broker = f"""
[mcp_servers.secondshift]
# The broker enforces the run's approved tools. Unattended runs cannot
# answer a second approval prompt from the CLI.
default_tools_approval_mode = "approve"
# Without the broker the run has no tools. Wait for it, and fail the run
# if it never answers, instead of letting the model report missing tools.
required = true
startup_timeout_sec = {BROKER_STARTUP_TIMEOUT_SECONDS}
url = {json.dumps(config["broker_url"])}
bearer_token_env_var = "{BROKER_TOKEN_VAR}"
"""
    # Codex keeps its refreshed login in CODEX_HOME, which is /auth, so its
    # config must live there too. It is rewritten on every run, so nothing a
    # previous run wrote survives.
    private_file(
        outcomes.AUTH / "config.toml",
        f"""approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"

[features]
shell_tool = false
unified_exec = false
js_repl = false
code_mode = false
multi_agent = false
plugins = false
apps = false
computer_use = false
image_generation = false
apply_patch_freeform = false
# Start the turn only after the broker has listed its tools.
incremental_tools = false

# Some models ask for Codex's multi-agent tools, and their request wins over
# features.multi_agent. Only this setting removes the tools for every model.
# Codex sub-agents report no token usage, and nesting limits do not hold.
[agents]
enabled = false
{broker}""",
    )
    argv = ["codex", "exec", "--skip-git-repo-check", "--ephemeral"]
    argv += ["--strict-config", "--json", "-"]
    return argv, ({BROKER_TOKEN_VAR: config["broker_token"]} if broker else {})


def read_codex(event: dict[str, Any], outcome: Outcome) -> None:
    kind = event.get("type")
    item = event.get("item") or {}
    if kind == "item.completed" and item.get("type") == "agent_message":
        outcome.report = str(item.get("text", ""))[:MAX_REPORT_CHARS]
    elif kind == "turn.completed":
        outcome.usage = numbers(event.get("usage") or {})
        outcome.finished = bool(outcome.report.strip())
    elif kind == "error":
        outcome.error = str(event.get("message", ""))
    elif kind == "turn.failed":
        outcome.error = str((event.get("error") or {}).get("message", ""))
