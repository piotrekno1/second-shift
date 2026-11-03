"""Why a subscription run or a login check ended.

The agent image ships this file next to agent.py, so it may use only the
standard library.
"""

from enum import StrEnum


class AgentOutcome(StrEnum):
    COMPLETED = "completed"
    RECONNECT_REQUIRED = "reconnect_required"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TIMED_OUT = "timed_out"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    TURN_BUDGET_EXHAUSTED = "turn_budget_exhausted"
    AGENT_OUTPUT_LIMIT = "agent_output_limit"
    INVALID_AGENT_OUTPUT = "invalid_agent_output"
    AGENT_FAILED = "agent_failed"
