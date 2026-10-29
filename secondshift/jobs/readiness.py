"""Why a job cannot run yet, in plain words."""

from typing import Any

from secondshift.jobs.models import Job
from secondshift.jobs.planning import build_execution_plan

REASON_TEXT = {
    "no_grant": "waits for an owner's approval",
    "grant_pending": "waits for an owner's approval",
    "grant_denied": "was denied by an owner",
    "connection_expired": "uses a connection whose sign-in expired",
    "connection_revoked": "uses a connection that was disconnected",
    "connection_workspace_mismatch": "is approved for a connection that does not match",
    "connection_kind_mismatch": "is approved for a connection that does not match",
    "connection_binding_mismatch": "is approved for a connection that does not match",
    "tools_not_granted": "requests tools the owner did not approve",
    "tools_unavailable": "requests tools the connection does not offer",
}
OTHER_REASON = "cannot reach what the job needs through its connection"


def plan_problems(job: Job, plan: dict[str, Any]) -> list[str]:
    problems = []
    if not job.repository.enabled:
        problems.append("The repository is disabled.")
    if plan["model"] is None:
        problems.append("No default model provider is connected.")
    for capability in plan["capabilities"]:
        if not capability["required"] or capability["authorized"]:
            continue
        texts = dict.fromkeys(
            REASON_TEXT.get(reason, OTHER_REASON) for reason in capability["reasons"]
        )
        problems.append(f"`{capability['binding']}` {' and '.join(texts)}.")
    return problems


def readiness_problems(job: Job) -> list[str]:
    """Empty when Run now would start the job."""
    return plan_problems(job, build_execution_plan(job))
