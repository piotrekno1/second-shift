"""Execution plans for tests that need a run without planning one."""

from typing import Any

from secondshift.config.constants import AgentBackend
from secondshift.jobs.plan import ExecutionPlan

BASE: dict[str, Any] = {
    "execution": {
        "backend": AgentBackend.PYDANTIC,
        "label": "Model API",
        "ready": True,
        "problem": "",
    },
    "ready": True,
    "agent": {
        "limits": {
            "max_turns": 24,
            "max_tool_calls": 150,
            "max_tool_result_chars": 65_536,
            "token_budget": 2_000_000,
            "timeout_seconds": 900,
        },
        "tools": [],
        "subagents": 0,
    },
    "job": {"key": "review", "skill_path": "SKILL.md", "config_hash": "hash"},
    "repository": {"full_name": "example/project", "config_sha": "a" * 40},
    "runner": {
        "image": "runner:test",
        "network_policy": "none",
        "collect_changes": False,
    },
    "pull_request": None,
    "issues": None,
    "model": None,
    "capabilities": [],
}


MODEL: dict[str, Any] = {
    "connection_id": 1,
    "connection_name": "Model",
    "provider": "anthropic",
    "family": "anthropic",
    "default_model": "claude-opus-5-5",
    "allowed_models": [],
}


CAPABILITY: dict[str, Any] = {
    "binding": "observability",
    "kind": "mcp",
    "required": True,
    "requested_tools": [],
    "connection_id": None,
    "connection_name": None,
    "effective_tools": [],
    "authorized": False,
    "reasons": [],
}


def merged(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result


def stored_plan(**changes: Any) -> dict[str, Any]:
    """A ready plan, as a run stores it. Nested dicts in `changes` are merged."""
    return ExecutionPlan.model_validate(merged(BASE, changes)).stored()
