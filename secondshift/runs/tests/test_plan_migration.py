from secondshift.jobs.plan import ExecutionPlan
from secondshift.runs.migration_helpers import normalized_plan_v1

FALLBACK = {
    "job": {"key": "review", "skill_path": "SKILL.md", "config_hash": "hash"},
    "repository": {"full_name": "example/project", "config_sha": "a" * 40},
}

# The shape v0.1.0a1 stored.
FIRST_RELEASE_PLAN = {
    "version": 2,
    "ready": True,
    "agent": {
        "driver": "pydantic-ai",
        "protocol": "pydantic-ai-v1",
        "limits": {"max_turns": 8, "max_tool_calls": 20, "max_tool_result_chars": 99},
        "tools": ["workspace.read_file"],
    },
    "job": {"key": "review", "skill_path": "SKILL.md", "config_hash": "hash"},
    "repository": {"full_name": "example/project", "config_sha": "b" * 40},
    "policy": {"mode": "workspace-grants-v1"},
    "runner": {"image": "runner:1", "network_policy": "none"},
    "model": {
        "connection_id": 3,
        "connection_name": "Anthropic",
        "provider": "anthropic",
        "default_model": "m-1",
        "allowed_models": [],
    },
    "capabilities": [
        {
            "binding": "observability",
            "kind": "mcp",
            "required": True,
            "requested_tools": ["query_metrics"],
            "connection_id": 4,
            "connection_name": "Grafana",
            "effective_tools": ["query_metrics"],
            "authorized": True,
            "reasons": [],
        }
    ],
}


def test_a_first_release_plan_reads_as_a_current_plan(settings) -> None:
    settings.MODEL_RUN_TOKEN_BUDGET = 1_000
    settings.RUN_TIMEOUT_SECONDS = 600

    plan = ExecutionPlan.model_validate(
        normalized_plan_v1(FIRST_RELEASE_PLAN, FALLBACK)
    )

    assert plan.ready is True
    assert plan.execution.backend == "pydantic"
    assert plan.agent.limits.max_turns == 8
    assert plan.agent.limits.token_budget == 1_000
    assert plan.agent.limits.timeout_seconds == 600
    assert plan.repository.config_sha == "b" * 40
    assert plan.model is not None and plan.model.default_model == "m-1"
    assert plan.capabilities[0].effective_tools == ("query_metrics",)
    assert plan.pull_request is None


def test_a_run_without_a_plan_gets_one_that_is_not_ready() -> None:
    plan = ExecutionPlan.model_validate(normalized_plan_v1({}, FALLBACK))

    assert plan.ready is False
    assert plan.job.key == "review"
    assert plan.repository.full_name == "example/project"
    assert plan.capabilities == ()


def test_a_current_plan_is_unchanged() -> None:
    current = normalized_plan_v1(FIRST_RELEASE_PLAN, FALLBACK)

    assert normalized_plan_v1(current, FALLBACK) == current
