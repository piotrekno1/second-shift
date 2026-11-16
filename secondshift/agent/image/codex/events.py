"""How to read Codex's JSON events into a run's outcome."""

from typing import Any

from secondshift.agent.image.outcomes import MAX_REPORT_CHARS, Outcome, numbers


def read(event: dict[str, Any], outcome: Outcome) -> None:
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
