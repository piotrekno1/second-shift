"""How the image reads CLI events into a run's outcome, and its limits."""

import sys
import time
from typing import Any

import pytest

from secondshift.agent.image import agent, outcomes
from secondshift.agent.image.claude import events as claude_events
from secondshift.agent.image.claude.subagent_hook import WORKER
from secondshift.agent.image.codex import events as codex_events
from secondshift.agent.tests.test_commands import TOKEN


def outcome_of(read: Any, events: list[dict[str, Any]], exit_code: int = 0) -> Any:
    outcome = outcomes.Outcome()
    for event in events:
        read(event, outcome)
    return agent.result(outcome, exit_code)


def test_agent_events_keep_only_final_report_and_numeric_usage() -> None:
    done = outcome_of(
        claude_events.read,
        [
            {
                "type": "result",
                "result": "Done",
                "is_error": False,
                "usage": {"input_tokens": 20, "secret": TOKEN},
                "stderr": TOKEN,
            }
        ],
    )
    assert done == {
        "report": "Done",
        "succeeded": True,
        "reason": "completed",
        "usage": {"input_tokens": 20},
        "subagents": [],
        "models": [],
    }
    codex_done = outcome_of(
        codex_events.read,
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi"}},
            {"type": "turn.completed", "usage": {"output_tokens": 3}},
        ],
    )
    assert codex_done["report"] == "Hi" and codex_done["succeeded"] is True
    # A failing CLI exit code never counts as success.
    assert outcome_of(codex_events.read, [], exit_code=0)["reason"] == "agent_failed"


def reply(message_id: str, sub_agent: bool = False, **usage: int) -> dict[str, Any]:
    return {
        "type": "assistant",
        "parent_tool_use_id": "toolu_1" if sub_agent else None,
        "message": {"id": message_id, "usage": usage},
    }


def test_claude_usage_adds_up_each_reply_once_with_sub_agents() -> None:
    outcome = outcomes.Outcome()
    events = [
        # One reply can arrive as several events with the same usage.
        reply("msg_1", input_tokens=2, cache_read_input_tokens=5000),
        reply("msg_1", input_tokens=2, cache_read_input_tokens=5000),
        reply("msg_2", sub_agent=True, input_tokens=2, cache_read_input_tokens=1000),
        reply("msg_3", sub_agent=True, input_tokens=2, output_tokens=16),
    ]
    for event in events:
        claude_events.read(event, outcome)

    # Counted as replies arrive, so a budget can stop the run before it ends.
    assert outcome.usage == {
        "input_tokens": 6,
        "cache_read_input_tokens": 6000,
        "output_tokens": 16,
    }

    # The final usage leaves out sub-agents. The per-model usage includes them.
    # The largest of each count is kept.
    claude_events.read(
        {
            "type": "result",
            "result": "Done",
            "is_error": False,
            "usage": {"input_tokens": 4, "output_tokens": 900},
            "modelUsage": {
                "claude-haiku": {"outputTokens": 1500, "costUSD": 0.01},
                "claude-sonnet": {"inputTokens": 3, "cacheReadInputTokens": 7000},
            },
        },
        outcome,
    )
    assert outcome.usage == {
        "input_tokens": 6,
        "cache_read_input_tokens": 7000,
        "output_tokens": 1500,
    }


def test_claude_runs_keep_the_models_that_answered_the_agents_first() -> None:
    main = reply("msg_1", input_tokens=1)
    main["message"]["model"] = "claude-opus-5-5"
    helper = reply("msg_2", sub_agent=True, input_tokens=1)
    helper["message"]["model"] = "claude-opus-5-5"
    # Claude Code marks replies it made up itself, like an error notice.
    synthetic = reply("msg_3", input_tokens=0)
    synthetic["message"]["model"] = "<synthetic>"
    result = {
        "type": "result",
        "result": "Done",
        "is_error": False,
        "modelUsage": {
            "claude-haiku-5-5": {"inputTokens": 3},
            "claude-opus-5-5": {"inputTokens": 2},
            "--model=evil": {"inputTokens": 1},
        },
    }

    done = outcome_of(claude_events.read, [main, helper, synthetic, result])

    assert done["models"] == ["claude-opus-5-5", "claude-haiku-5-5"]


def test_a_run_keeps_at_most_a_few_models() -> None:
    outcome = outcomes.Outcome()
    for number in range(outcomes.MAX_MODELS + 5):
        outcome.add_model(f"model-{number}")
    outcome.add_model(None)
    outcome.add_model("x" * 201)

    assert len(outcome.models) == outcomes.MAX_MODELS


def test_the_token_budget_stops_claude_mid_run(monkeypatch) -> None:
    """A CLI that keeps going past the budget is stopped, not waited for."""
    events = [
        # Cache reads count as a tenth: 600 each.
        reply("msg_1", cache_read_input_tokens=6000),
        reply("msg_2", sub_agent=True, cache_read_input_tokens=6000),
    ]
    script = (
        "import json, sys, time\n"
        f"for event in {events!r}:\n"
        "    print(json.dumps(event), flush=True)\n"
        "time.sleep(60)\n"
    )

    def start(config: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
        return [sys.executable, "-c", script], {}

    monkeypatch.setitem(
        agent.BACKENDS, "claude_code", (sys.executable, start, claude_events.read)
    )
    started = time.monotonic()

    finished = agent.run(
        {"backend": "claude_code", "prompt": "Audit", "token_budget": 1000}
    )

    assert time.monotonic() - started < 30
    assert finished["reason"] == "token_budget_exhausted"
    assert finished["succeeded"] is False
    assert finished["usage"] == {"cache_read_input_tokens": 12000}


@pytest.mark.parametrize(
    ("usage", "budget"),
    [
        # Claude Code reports cache reads beside fresh input.
        (
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 4,
                "cache_read_input_tokens": 91,
            },
            29,
        ),
        # Codex reports cached input inside input.
        ({"input_tokens": 101, "cached_input_tokens": 91, "output_tokens": 5}, 25),
        ({}, 0),
    ],
)
def test_cache_reads_count_as_a_tenth_toward_the_budget(
    usage: dict[str, int], budget: int
) -> None:
    assert outcomes.usage_budget(usage) == budget


def test_a_codex_run_with_mostly_cached_input_finishes(monkeypatch) -> None:
    usage = {"input_tokens": 5000, "cached_input_tokens": 4900, "output_tokens": 50}
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}},
        {"type": "turn.completed", "usage": usage},
    ]
    script = (
        "import json\n"
        f"for event in {events!r}:\n"
        "    print(json.dumps(event), flush=True)\n"
    )

    def start(config: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
        return [sys.executable, "-c", script], {}

    monkeypatch.setitem(
        agent.BACKENDS, "codex", (sys.executable, start, codex_events.read)
    )

    # 100 fresh input, 50 output and 490 for the cache reads.
    finished = agent.run({"backend": "codex", "prompt": "Audit", "token_budget": 1000})

    assert finished["succeeded"] is True
    assert finished["usage"] == usage


@pytest.mark.parametrize(
    ("read", "events", "reason"),
    [
        (
            claude_events.read,
            [
                {
                    "type": "result",
                    "is_error": True,
                    "result": "API Error: 401 Invalid bearer token",
                }
            ],
            "reconnect_required",
        ),
        (
            claude_events.read,
            [{"type": "result", "subtype": "error_max_turns", "is_error": True}],
            "turn_budget_exhausted",
        ),
        (
            codex_events.read,
            [{"type": "turn.failed", "error": {"message": "You hit your usage limit"}}],
            "quota_exhausted",
        ),
    ],
)
def test_failed_agents_return_a_reason_but_never_their_error_text(
    read: Any, events: list[dict[str, Any]], reason: str
) -> None:
    failed = outcome_of(read, events)
    assert failed["reason"] == reason
    assert failed["succeeded"] is False
    assert failed["report"] == ""


def start(**fields: Any) -> dict[str, Any]:
    task = {
        "subagent_type": WORKER,
        "prompt": "Check it",
        "run_in_background": False,
    } | fields
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": task}


def subagent_reply(
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
    fields = {"subagent_type": WORKER, "run_in_background": False} | task
    return call(call_id, "Agent", **fields)


def handback(call_id: str) -> dict[str, Any]:
    content = [{"type": "tool_result", "tool_use_id": call_id, "content": "Report"}]
    return {"type": "user", "parent_tool_use_id": None, "message": {"content": content}}


def read(events: list[dict[str, Any]], subagents: int = 2, max_turns: int = 20):
    outcome = outcomes.Outcome(max_replies=max_turns, max_subagents=subagents)
    for event in events:
        claude_events.read(event, outcome)
        if outcome.reason:
            break
    return outcome


def test_each_subagent_reports_what_it_used() -> None:
    outcome = read(
        [
            subagent_reply("m1", None, worker("start-1")),
            subagent_reply("m2", "start-1", call("read-1"), call("read-2")),
            # One reply can arrive as several events.
            subagent_reply("m2", "start-1", call("read-2")),
            subagent_reply("m3", "start-1"),
            handback("start-1"),
            subagent_reply("m4", None, worker("start-2")),
            subagent_reply("m5", "start-2", call("read-3")),
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
            subagent_reply(
                "m1", None, call("s1", "Agent", subagent_type="general-purpose")
            ),
            subagent_reply("m2", "s1"),
        ],
        # A sub-agent that starts its own.
        [
            subagent_reply("m1", None, worker("s1")),
            subagent_reply("m2", "s1", worker("s2")),
            subagent_reply("m3", "s2"),
        ],
        # A background sub-agent.
        [
            subagent_reply("m1", None, worker("s1", run_in_background=True)),
            subagent_reply("m2", "s1"),
        ],
        # Without the flag, Claude Code runs it in the background too.
        [
            subagent_reply("m1", None, call("s1", "Agent", subagent_type=WORKER)),
            subagent_reply("m2", "s1"),
        ],
        # More sub-agents than the run allows.
        [
            subagent_reply("m1", None, worker("s1"), worker("s2"), worker("s3")),
            subagent_reply("m2", "s1"),
            subagent_reply("m3", "s2"),
            subagent_reply("m4", "s3"),
        ],
        # Activity under no start the run's agent asked for.
        [subagent_reply("m1", "unknown")],
    ],
)
def test_a_subagent_the_hook_should_have_refused_stops_the_run(
    events: list[dict[str, Any]],
) -> None:
    assert read(events).reason == "subagent_refused"


def test_subagent_replies_count_toward_the_runs_turns() -> None:
    # Claude Code's --max-turns counts only the run's own agent.
    events = [
        subagent_reply("m1", None, worker("s1")),
        subagent_reply("m2", "s1", call("r1")),
        subagent_reply("m3", "s1", call("r2")),
        subagent_reply("m4", "s1"),
    ]

    assert read(events, max_turns=4).reason == ""
    assert read(events, max_turns=3).reason == "turn_budget_exhausted"


def test_runs_without_subagents_keep_claude_codes_turn_limit() -> None:
    outcome = outcomes.Outcome()
    for number in range(50):
        claude_events.read(subagent_reply(f"m{number}"), outcome)

    assert outcome.reason == "" and outcome.subagents == {}


def test_the_turn_limit_stops_the_cli_mid_run(monkeypatch) -> None:
    events = [
        subagent_reply("m1", None, worker("s1")),
        subagent_reply("m2", "s1"),
        subagent_reply("m3", "s1"),
    ]
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
            claude_events.read,
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


# Codex 0.166's own words when its sign-in can't be refreshed.
CODEX_REFRESH_ERRORS = [
    "Your access token could not be refreshed because your refresh token has "
    "expired. Please log out and sign in again.",
    "Your access token could not be refreshed because your refresh token was "
    "already used. Please log out and sign in again.",
    "Your access token could not be refreshed because you have since logged out "
    "or signed in to another account. Please sign in again.",
    "Your access token could not be refreshed. Please log out and sign in again.",
]


@pytest.mark.parametrize("message", CODEX_REFRESH_ERRORS)
@pytest.mark.parametrize(
    "event",
    [
        lambda message: {"type": "error", "message": message},
        lambda message: {"type": "turn.failed", "error": {"message": message}},
    ],
)
def test_a_codex_login_that_cannot_refresh_needs_a_reconnect(
    message: str, event: Any
) -> None:
    failed = outcome_of(codex_events.read, [event(message)])
    assert failed["reason"] == "reconnect_required"
    assert failed["report"] == ""
