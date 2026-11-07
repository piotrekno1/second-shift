"""Claude Code calls this before it starts a sub-agent, and it may refuse.

Claude Code runs it as a PreToolUse hook for its Agent tool, with the run's
limit and a counter file as arguments. It allows only the server-owned worker,
only from the run's own agent, in the foreground, up to the limit. Any error
exits with 2, which makes Claude Code refuse the call. Other exit codes would
let it through. The agent reads the event stream too, and stops the CLI if a
sub-agent runs that this should have refused.

Claude Code runs this file by path, so it uses only the standard library.
"""

import fcntl
import json
import sys
from pathlib import Path
from typing import Any

# The one sub-agent type a run may start. Its tools are the broker's only.
WORKER = "secondshift-worker"
# Claude Code's name for the tool in --tools, and in tool calls.
AGENT_TOOLS = ("Task", "Agent")


def refusal(event: dict[str, Any], limit: int, counter: Path) -> str:
    """Why this start is refused, or an empty string to allow it."""
    if event.get("agent_id"):
        return "A sub-agent cannot start sub-agents. Do this yourself."
    task = event.get("tool_input") or {}
    if task.get("subagent_type") != WORKER:
        return f"Start sub-agents with subagent_type {WORKER}."
    if task.get("run_in_background"):
        return "Start sub-agents in the foreground and wait for their report."
    with counter.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        started = int(stream.read() or 0)
        if started >= limit:
            return (
                f"This run has started the most sub-agents it may, {limit}. "
                "Do the rest yourself."
            )
        stream.seek(0)
        stream.truncate()
        stream.write(str(started + 1))
    return ""


def main() -> None:
    try:
        limit, counter = int(sys.argv[1]), Path(sys.argv[2])
        reason = refusal(json.load(sys.stdin), limit, counter)
    except Exception:
        print("The sub-agent check failed.", file=sys.stderr)
        sys.exit(2)
    if reason:
        decision = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
        print(json.dumps({"hookSpecificOutput": decision}))


if __name__ == "__main__":
    main()
