"""How to read Claude Code's stream-json events into a run's outcome."""

from typing import Any

from secondshift.agent.image.claude.subagent_hook import AGENT_TOOLS, WORKER
from secondshift.agent.image.outcomes import (
    MAX_REPORT_CHARS,
    TOKEN_KEYS,
    AgentOutcome,
    Outcome,
    numbers,
)

# How Claude Code names the same counts in its per-model usage.
MODEL_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
}


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


def tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and isinstance(block.get("id"), str)
    ]


def allowed_start(call: dict[str, Any], parent: Any) -> bool:
    """The hook's rules, checked again from the stream."""
    task = call.get("input") or {}
    return (
        parent is None
        and isinstance(task, dict)
        and task.get("subagent_type") == WORKER
        and task.get("run_in_background") is False
    )


def read_subagents(
    event: dict[str, Any], message: dict[str, Any], new: bool, outcome: Outcome
) -> None:
    """Count what each sub-agent uses, and stop a run that breaks its limits.

    The hook refuses sub-agents the run may not start. This catches one that
    runs anyway, such as when the hook could not run. Replies of sub-agents
    carry the ID of the tool call that started them.
    """
    if outcome.max_replies is None:
        return
    parent = event.get("parent_tool_use_id")
    calls = [
        call for call in tool_calls(message) if call["id"] not in outcome.tool_calls
    ]
    outcome.tool_calls.update(call["id"] for call in calls)
    for call in calls:
        if call.get("name") in AGENT_TOOLS:
            outcome.starts[call["id"]] = allowed_start(call, parent)
    if parent is not None:
        if not outcome.starts.get(parent):
            outcome.reason = AgentOutcome.SUBAGENT_REFUSED
            return
        used = outcome.subagents.setdefault(
            parent, {"requests": 0, "tool_calls": 0, "finished": 0}
        )
        used["requests"] += int(new)
        used["tool_calls"] += len(calls)
        if len(outcome.subagents) > outcome.max_subagents:
            outcome.reason = AgentOutcome.SUBAGENT_REFUSED
            return
    # Claude Code's own turn limit leaves out sub-agents' replies.
    if len(outcome.replies) > outcome.max_replies:
        outcome.reason = AgentOutcome.TURN_BUDGET_EXHAUSTED


def read_handbacks(event: dict[str, Any], outcome: Outcome) -> None:
    """Mark sub-agents whose report reached the run's agent."""
    if event.get("parent_tool_use_id") is not None:
        return
    content = (event.get("message") or {}).get("content")
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        used = outcome.subagents.get(block.get("tool_use_id"))
        if used is not None and not block.get("is_error"):
            used["finished"] = 1


def read(event: dict[str, Any], outcome: Outcome) -> None:
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
        new = message_id not in outcome.replies
        usage = numbers(message.get("usage") or {})
        earlier = outcome.replies.get(message_id, {})
        outcome.replies[message_id] = {
            key: max(usage.get(key, 0), earlier.get(key, 0)) for key in TOKEN_KEYS
        }
        outcome.usage = reply_totals(outcome.replies)
        read_subagents(event, message, new, outcome)
        return
    if event.get("type") == "user":
        read_handbacks(event, outcome)
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
