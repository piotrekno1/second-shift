"""Why a job cannot run yet, in plain words."""

from secondshift.jobs.environments import environment_missing
from secondshift.jobs.execution_plan import ExecutionPlan, PlanReason
from secondshift.jobs.models import Job
from secondshift.jobs.planner import build_execution_plan
from secondshift.repositories.models import Repository

MISMATCH = "is approved for a connection that does not match"
REASON_TEXT = {
    PlanReason.NO_GRANT: "waits for an owner's approval",
    PlanReason.GRANT_PENDING: "waits for an owner's approval",
    PlanReason.GRANT_DENIED: "was denied by an owner",
    PlanReason.CONNECTION_MISSING: "has no approved connection",
    PlanReason.CONNECTION_EXPIRED: "uses a connection whose sign-in expired",
    PlanReason.CONNECTION_REVOKED: "uses a connection that was disconnected",
    PlanReason.CONNECTION_WORKSPACE_MISMATCH: MISMATCH,
    PlanReason.CONNECTION_KIND_MISMATCH: MISMATCH,
    PlanReason.CONNECTION_BINDING_MISMATCH: MISMATCH,
    PlanReason.TOOLS_NOT_GRANTED: "requests tools the owner did not approve",
    PlanReason.TOOLS_UNAVAILABLE: "requests tools the connection does not offer",
    PlanReason.TOOL_RULES_BROKEN: (
        "has tool restrictions that no longer fit the connection"
    ),
}
OTHER_REASON = "cannot reach what the job needs through its connection"


def environment_problem(name: str) -> str:
    """Why a job naming an environment the server lacks cannot run."""
    return (
        f"This server has no {name} environment. Ask the server admin to add "
        "it, or change environment in .secondshift.yaml."
    )


def execution_problems(repository: Repository, plan: ExecutionPlan) -> list[str]:
    problems = []
    if not repository.accepts_runs:
        problems.append("The repository is disabled.")
    if not plan.execution.ready:
        problems.append(plan.execution.problem)
    if environment_missing(plan.runner.environment):
        problems.append(environment_problem(plan.runner.environment))
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
