"""Start Claude Code or Codex in the agent container and report the outcome.

The runner sends one JSON line on stdin. The CLI reaches SecondShift tools only
through the run's MCP broker over HTTP. Raw CLI events and stderr can contain
credentials or tool payloads, so only the final report and numeric usage leave
the container.
"""

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from secondshift.runner.agent_image.outcomes import AgentOutcome

TMP = Path("/tmp")
AUTH = Path("/auth")
MAX_CONFIG_BYTES = 512 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_REPORT_CHARS = 200_000
TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
# How Claude Code names the same counts in its per-model usage.
MODEL_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
}
BROKER_TOKEN_VAR = "SECONDSHIFT_BROKER_TOKEN"
BROKER_STARTUP_TIMEOUT_SECONDS = 60


@dataclass
class Outcome:
    report: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    # The CLI gave a final answer.
    finished: bool = False
    # Set when a limit stopped the CLI early.
    reason: str = ""
    # The last error the CLI reported, used to explain a failure.
    error: str = ""
    # Token usage of each model reply so far, by message ID.
    replies: dict[str, dict[str, int]] = field(default_factory=dict)


def numbers(values: dict[str, Any]) -> dict[str, int]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


def private_file(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o600)


def claude(config: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    """Claude Code with no built-in tools and only the run's broker.

    A login check has no broker, so it has no tools at all.
    """
    argv = [
        "claude",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--tools",
        "",
        "--strict-mcp-config",
    ]
    if config.get("broker_url"):
        servers = {
            "secondshift": {
                "type": "http",
                "url": config["broker_url"],
                "headers": {"Authorization": f"Bearer {config['broker_token']}"},
            }
        }
        private_file(TMP / "mcp.json", json.dumps({"mcpServers": servers}))
        argv += [
            "--mcp-config",
            str(TMP / "mcp.json"),
            "--allowedTools",
            "mcp__secondshift__*",
        ]
    argv += [
        "--max-turns",
        str(config["max_turns"]),
        "--setting-sources",
        "",
        "--no-session-persistence",
    ]
    token = (AUTH / "claude-token").read_text().strip()
    return argv, {"CLAUDE_CODE_OAUTH_TOKEN": token}


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
        AUTH / "config.toml",
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
{broker}""",
    )
    argv = ["codex", "exec", "--skip-git-repo-check", "--ephemeral"]
    argv += ["--strict-config", "--json", "-"]
    return argv, ({BROKER_TOKEN_VAR: config["broker_token"]} if broker else {})


def reply_totals(replies: dict[str, dict[str, int]]) -> dict[str, int]:
    totals = {
        key: sum(usage.get(key, 0) for usage in replies.values()) for key in TOKEN_KEYS
    }
    return {key: value for key, value in totals.items() if value}


def model_usage_totals(model_usage: Any) -> dict[str, int]:
    """The result's per-model usage, summed. It includes sub-agents."""
    totals: dict[str, int] = {}
    if not isinstance(model_usage, dict):
        return totals
    for usage in model_usage.values():
        if not isinstance(usage, dict):
            continue
        for name, key in MODEL_USAGE_KEYS.items():
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value:
                totals[key] = totals.get(key, 0) + value
    return totals


def read_claude(event: dict[str, Any], outcome: Outcome) -> None:
    """Claude Code reports usage on each reply, sub-agents' replies included.

    Adding them up as they arrive lets the budget stop the CLI mid-run. One
    reply can arrive as several events, so each message ID counts once, at its
    largest. Replies report output tokens before they finish, so this total
    runs a little low. The result's usage leaves out sub-agents, and its
    per-model usage includes them, so the run keeps the largest of each count.
    """
    if event.get("type") == "assistant":
        message = event.get("message") or {}
        message_id = message.get("id")
        if not isinstance(message_id, str):
            return
        usage = numbers(message.get("usage") or {})
        earlier = outcome.replies.get(message_id, {})
        outcome.replies[message_id] = {
            key: max(usage.get(key, 0), earlier.get(key, 0)) for key in TOKEN_KEYS
        }
        outcome.usage = reply_totals(outcome.replies)
        return
    if event.get("type") != "result":
        return
    final = numbers(event.get("usage") or {})
    for totals in (
        reply_totals(outcome.replies),
        model_usage_totals(event.get("modelUsage")),
    ):
        for key, value in totals.items():
            final[key] = max(final.get(key, 0), value)
    outcome.usage = final
    if event.get("subtype") == "error_max_turns":
        outcome.reason = AgentOutcome.TURN_BUDGET_EXHAUSTED
    elif event.get("is_error", True):
        outcome.error = str(event.get("result") or event.get("errors") or "")
    else:
        outcome.report = str(event.get("result", ""))[:MAX_REPORT_CHARS]
        outcome.finished = bool(outcome.report.strip())


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


# The command and extra environment for one CLI.
Start = Callable[[dict[str, Any]], tuple[list[str], dict[str, str]]]
# Updates the outcome from one CLI event.
Read = Callable[[dict[str, Any], Outcome], None]
BACKENDS: dict[str, tuple[str, Start, Read]] = {
    "claude_code": ("claude", claude, read_claude),
    "codex": ("codex", codex, read_codex),
}


def failure_reason(error: str) -> str:
    """A reason the control plane can explain, from the CLI's last error."""
    text = error.lower()
    if any(word in text for word in ("rate limit", "usage limit", "quota")):
        return AgentOutcome.QUOTA_EXHAUSTED
    if any(word in text for word in ("401", "unauthorized", "authenticat", "oauth")):
        return AgentOutcome.RECONNECT_REQUIRED
    return AgentOutcome.AGENT_FAILED


def result(outcome: Outcome, exit_code: int) -> dict[str, Any]:
    if outcome.reason:
        reason = outcome.reason
    elif outcome.finished and exit_code == 0:
        reason = AgentOutcome.COMPLETED
    else:
        reason = failure_reason(outcome.error)
    succeeded = reason == AgentOutcome.COMPLETED
    return {
        # A failed CLI's text is an error message, never the run's report.
        "report": outcome.report if succeeded else "",
        "succeeded": succeeded,
        "reason": reason,
        "usage": outcome.usage,
    }


def version(binary: str) -> str:
    try:
        found = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return found.stdout.strip()[:100] or "unknown"


def run(config: dict[str, Any]) -> dict[str, Any]:
    binary, start, read = BACKENDS[config["backend"]]
    argv, env = start(config)
    outcome = Outcome()
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, **env},
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        # A CLI that exits early closes its input. Its outcome says why.
        with contextlib.suppress(BrokenPipeError):
            process.stdin.write(config["prompt"].encode())
            process.stdin.close()
        while line := process.stdout.readline(MAX_EVENT_BYTES + 1):
            if len(line) > MAX_EVENT_BYTES:
                outcome.reason = AgentOutcome.AGENT_OUTPUT_LIMIT
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                read(event, outcome)
            if sum(outcome.usage.get(key, 0) for key in TOKEN_KEYS) >= int(
                config["token_budget"]
            ):
                outcome.reason = AgentOutcome.TOKEN_BUDGET_EXHAUSTED
                break
        if not outcome.reason:
            # A CLI that hangs after its last event is killed below and fails.
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
        exit_code = process.wait()
    return result(outcome, exit_code) | {"versions": {binary: version(binary)}}


def main() -> None:
    os.umask(0o077)
    config = json.loads(sys.stdin.buffer.readline(MAX_CONFIG_BYTES))
    print(json.dumps(run(config)))


if __name__ == "__main__":
    main()
