"""Claude Code sub-agents: the server-owned worker, the hook and the stream checks."""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from secondshift.runner import agents
from secondshift.runner.agent_credentials import (
    encrypt_agent_auth,
    provisioning_public_key,
)
from secondshift.runner.agent_image import agent, claude, outcomes, subagent_hook
from secondshift.runner.agent_image.subagent_hook import WORKER
from secondshift.runner.agent_protocol import AgentInput
from secondshift.runner.models import RunnerSession
from secondshift.runner.tests.test_agents import BROKER, IMAGE, TOKEN


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
    argv, _ = claude.claude(BROKER | {"subagents": 2})

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
    argv, _ = claude.claude(config | {"max_turns": 1})

    assert option(argv, "--tools") == ""
    assert "--agents" not in argv and "--settings" not in argv


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
    task = {"subagent_type": WORKER, "prompt": "Check it"} | fields
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
        (start(run_in_background=True), "foreground"),
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


def reply(
    message_id: str, parent: str | None = None, *calls: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "parent_tool_use_id": parent,
        "message": {"id": message_id, "content": list(calls), "usage": {}},
    }


def call(call_id: str, name: str = "mcp__secondshift__read", **task: Any) -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": task}


def worker(call_id: str, **task: Any) -> dict[str, Any]:
    return call(call_id, "Agent", **({"subagent_type": WORKER} | task))


def handback(call_id: str) -> dict[str, Any]:
    content = [{"type": "tool_result", "tool_use_id": call_id, "content": "Report"}]
    return {"type": "user", "parent_tool_use_id": None, "message": {"content": content}}


def read(events: list[dict[str, Any]], subagents: int = 2, max_turns: int = 20):
    outcome = outcomes.Outcome(max_replies=max_turns, max_subagents=subagents)
    for event in events:
        claude.read_claude(event, outcome)
        if outcome.reason:
            break
    return outcome


def test_each_subagent_reports_what_it_used() -> None:
    outcome = read(
        [
            reply("m1", None, worker("start-1")),
            reply("m2", "start-1", call("read-1"), call("read-2")),
            # One reply can arrive as several events.
            reply("m2", "start-1", call("read-2")),
            reply("m3", "start-1"),
            handback("start-1"),
            reply("m4", None, worker("start-2")),
            reply("m5", "start-2", call("read-3")),
        ]
    )

    assert outcome.reason == ""
    assert list(outcome.subagents.values()) == [
        {"requests": 2, "tool_calls": 2, "finished": 1},
        {"requests": 1, "tool_calls": 1, "finished": 0},
    ]


@pytest.mark.parametrize(
    "events",
    [
        # A type the hook should have refused.
        [
            reply("m1", None, call("s1", "Agent", subagent_type="general-purpose")),
            reply("m2", "s1"),
        ],
        # A sub-agent that starts its own.
        [
            reply("m1", None, worker("s1")),
            reply("m2", "s1", worker("s2")),
            reply("m3", "s2"),
        ],
        # A background sub-agent.
        [reply("m1", None, worker("s1", run_in_background=True)), reply("m2", "s1")],
        # More sub-agents than the run allows.
        [
            reply("m1", None, worker("s1"), worker("s2"), worker("s3")),
            reply("m2", "s1"),
            reply("m3", "s2"),
            reply("m4", "s3"),
        ],
        # Activity under no start the run's agent asked for.
        [reply("m1", "unknown")],
    ],
)
def test_a_subagent_the_hook_should_have_refused_stops_the_run(
    events: list[dict[str, Any]],
) -> None:
    assert read(events).reason == "subagent_refused"


def test_subagent_replies_count_toward_the_runs_turns() -> None:
    # Claude Code's --max-turns counts only the run's own agent.
    events = [
        reply("m1", None, worker("s1")),
        reply("m2", "s1", call("r1")),
        reply("m3", "s1", call("r2")),
        reply("m4", "s1"),
    ]

    assert read(events, max_turns=4).reason == ""
    assert read(events, max_turns=3).reason == "turn_budget_exhausted"


def test_runs_without_subagents_keep_claude_codes_turn_limit() -> None:
    outcome = outcomes.Outcome()
    for number in range(50):
        claude.read_claude(reply(f"m{number}"), outcome)

    assert outcome.reason == "" and outcome.subagents == {}


def test_the_turn_limit_stops_the_cli_mid_run(monkeypatch) -> None:
    events = [reply("m1", None, worker("s1")), reply("m2", "s1"), reply("m3", "s1")]
    script = (
        "import json, time\n"
        f"for event in {events!r}:\n"
        "    print(json.dumps(event), flush=True)\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setitem(
        agent.BACKENDS,
        "claude_code",
        (
            sys.executable,
            lambda config: ([sys.executable, "-c", script], {}),
            claude.read_claude,
        ),
    )
    started = time.monotonic()

    finished = agent.run(
        {
            "backend": "claude_code",
            "prompt": "Audit",
            "token_budget": 1000,
            "max_turns": 2,
            "subagents": 1,
        }
    )

    assert time.monotonic() - started < 30
    assert finished["reason"] == "turn_budget_exhausted"
    assert finished["subagents"] == [{"requests": 2, "tool_calls": 0, "finished": 0}]


@pytest.mark.django_db
@pytest.mark.parametrize(("backend", "sent"), [("claude_code", 2), ("codex", 0)])
def test_the_runner_passes_subagents_only_to_claude_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
    backend: str,
    sent: int,
) -> None:
    settings.AGENT_BROKER_URL = "http://gateway:8001/gateway/agent/mcp"
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"
    RunnerSession.objects.create(
        session_id="run-1",
        run_id=1,
        repository="example/agents",
        commit_sha="a" * 40,
        image="test",
        container_name="sandbox",
        workspace_path=str(tmp_path / "workspace"),
        status="active",
    )
    monkeypatch.setattr(agents, "require_agent_configuration", lambda: IMAGE)
    monkeypatch.setattr(
        agents.DockerRunnerService, "reserve_command", Mock(return_value=None)
    )
    launched = Mock(return_value=agents.AgentOutput())
    monkeypatch.setattr(agents, "run_agent_container", launched)

    agents.execute_agent(
        "run-1",
        AgentInput(
            backend=backend,
            credential_key="a" * 32,
            auth=encrypt_agent_auth(provisioning_public_key(), TOKEN),
            broker_token="broker",
            prompt="Read skill",
            max_turns=5,
            token_budget=1000,
            timeout_seconds=60,
            subagents=2,
        ),
    )

    assert launched.call_args.args[2]["subagents"] == sent
