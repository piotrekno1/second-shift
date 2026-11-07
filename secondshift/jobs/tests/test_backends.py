from secondshift.config.constants import AgentBackend
from secondshift.jobs.planning import plan_backend


def test_each_backend_states_what_its_runs_can_do() -> None:
    # Changing one of these changes planning, prompts and run pages together.
    assert {
        backend: (
            backend.uses_model_api,
            backend.starts_subagents,
            backend.limits_model_calls,
        )
        for backend in AgentBackend
    } == {
        AgentBackend.PYDANTIC: (True, True, True),
        AgentBackend.CLAUDE_CODE: (False, True, True),
        AgentBackend.CODEX: (False, False, False),
    }


def test_plans_without_an_execution_block_use_the_model() -> None:
    assert plan_backend({}) is AgentBackend.PYDANTIC
    assert plan_backend({"execution": {"backend": "codex"}}) is AgentBackend.CODEX
