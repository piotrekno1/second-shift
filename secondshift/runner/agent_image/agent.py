"""Start Claude Code or Codex in the agent container and report the outcome.

The runner sends one JSON line on stdin. The CLI reaches SecondShift tools only
through the run's MCP broker over HTTP. Raw CLI events and stderr can contain
credentials or tool payloads, so only the final report and numeric usage leave
the container. Each CLI's command and event reader live in its own module.
"""

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from secondshift.runner.agent_image.claude import claude, read_claude
from secondshift.runner.agent_image.codex import codex, read_codex
from secondshift.runner.agent_image.outcomes import AgentOutcome, Outcome, usage_budget

MAX_CONFIG_BYTES = 512 * 1024
MAX_EVENT_BYTES = 1024 * 1024


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
        # Only counts, in the order the sub-agents started.
        "subagents": list(outcome.subagents.values()),
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
    if subagents := int(config.get("subagents", 0)):
        outcome.max_replies = int(config["max_turns"])
        outcome.max_subagents = subagents
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
            if outcome.reason:
                break
            if usage_budget(outcome.usage) >= int(config["token_budget"]):
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
