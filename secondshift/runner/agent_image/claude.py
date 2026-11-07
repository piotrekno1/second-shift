"""Claude Code: how to start it, and how to read its stream-json events."""

import json
from typing import Any

from secondshift.runner.agent_image import outcomes
from secondshift.runner.agent_image.outcomes import (
    MAX_REPORT_CHARS,
    TOKEN_KEYS,
    AgentOutcome,
    Outcome,
    numbers,
    private_file,
)

# How Claude Code names the same counts in its per-model usage.
MODEL_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
}


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
        private_file(outcomes.TMP / "mcp.json", json.dumps({"mcpServers": servers}))
        argv += [
            "--mcp-config",
            str(outcomes.TMP / "mcp.json"),
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
    token = (outcomes.AUTH / "claude-token").read_text().strip()
    return argv, {"CLAUDE_CODE_OAUTH_TOKEN": token}


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
