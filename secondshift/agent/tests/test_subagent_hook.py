"""The hook Claude Code runs before it starts a sub-agent."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from secondshift.agent.image.claude import subagent_hook
from secondshift.agent.image.claude.subagent_hook import WORKER


def hook(tmp_path: Path, event: Any, limit: int = 2) -> subprocess.CompletedProcess:
    """Run the hook as Claude Code does: a separate process reading stdin."""
    return subprocess.run(
        [sys.executable, subagent_hook.__file__, str(limit), str(tmp_path / "count")],
        input=event if isinstance(event, str) else json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
    )


def denial(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0
    if not result.stdout:
        return ""
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    return output["permissionDecisionReason"]


def start(**fields: Any) -> dict[str, Any]:
    task = {
        "subagent_type": WORKER,
        "prompt": "Check it",
        "run_in_background": False,
    } | fields
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": task}


def test_the_hook_allows_the_worker_up_to_the_limit(tmp_path: Path) -> None:
    assert denial(hook(tmp_path, start())) == ""
    assert denial(hook(tmp_path, start())) == ""
    assert "the most sub-agents it may, 2" in denial(hook(tmp_path, start()))


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        # Claude Code's own types can have the Agent tool, or more.
        (start(subagent_type="general-purpose"), "subagent_type"),
        (start(subagent_type=None), "subagent_type"),
        # A call from inside a sub-agent carries its agent ID.
        (start() | {"agent_id": "a1", "agent_type": WORKER}, "cannot start"),
        (start(run_in_background=True), "run_in_background set to false"),
        # Claude Code runs a start without the flag in the background.
        (
            {**start(), "tool_input": {"subagent_type": WORKER, "prompt": "Check"}},
            "run_in_background set to false",
        ),
    ],
)
def test_the_hook_refuses_other_starts(
    tmp_path: Path, event: dict[str, Any], reason: str
) -> None:
    assert reason in denial(hook(tmp_path, event))
    # A refused start does not use up the limit.
    assert not (tmp_path / "count").exists()


@pytest.mark.parametrize("event", ["not json", "[]", json.dumps({"tool_input": 1})])
def test_a_hook_that_fails_blocks_the_start(tmp_path: Path, event: str) -> None:
    # Claude Code lets the call through on any other exit code.
    assert hook(tmp_path, event).returncode == 2
