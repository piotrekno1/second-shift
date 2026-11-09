"""Why a job cannot run yet, in plain words."""

from secondshift.jobs.models import Job
from secondshift.jobs.plan import ExecutionPlan
from secondshift.jobs.planning import build_execution_plan
from secondshift.repositories.models import Repository

REASON_TEXT = {
    "no_grant": "waits for an owner's approval",
    "grant_pending": "waits for an owner's approval",
    "grant_denied": "was denied by an owner",
    "connection_missing": "has no approved connection",
    "connection_expired": "uses a connection whose sign-in expired",
    "connection_revoked": "uses a connection that was disconnected",
    "connection_workspace_mismatch": "is approved for a connection that does not match",
    "connection_kind_mismatch": "is approved for a connection that does not match",
    "connection_binding_mismatch": "is approved for a connection that does not match",
    "tools_not_granted": "requests tools the owner did not approve",
    "tools_unavailable": "requests tools the connection does not offer",
    "tool_rules_broken": "has tool restrictions that no longer fit the connection",
}
OTHER_REASON = "cannot reach what the job needs through its connection"


def execution_problems(repository: Repository, plan: ExecutionPlan) -> list[str]:
    problems = []
    if not repository.enabled:
        problems.append("The repository is disabled.")
    if not plan.execution.ready:
        problems.append(plan.execution.problem)
    for capability in plan.capabilities:
        if not capability.required or capability.authorized:
            continue
        texts = dict.fromkeys(
            REASON_TEXT.get(reason, OTHER_REASON) for reason in capability.reasons
        )
        problems.append(f"{capability.binding} {' and '.join(texts)}.")
    return problems


def readiness_problems(job: Job) -> list[str]:
    """Empty when Run now would start the job."""
    return execution_problems(job.repository, build_execution_plan(job))
