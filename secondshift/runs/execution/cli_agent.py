"""Run a subscription job through the agent CLI, in one step that never repeats."""

import hashlib
import secrets
from typing import Any

from dbos import DBOS
from django.utils import timezone

from secondshift.connections.agents import AgentConnectionError, check_agent_connection
from secondshift.connections.models import AgentConnection
from secondshift.runner.agent_image.outcomes import AgentOutcome
from secondshift.runner.agent_protocol import AgentInput
from secondshift.runner.client import get_runner_client
from secondshift.runs.dao import (
    add_event,
    create_lease,
    end_lease,
    get_run,
    run_has_lease,
)
from secondshift.runs.execution.agent_broker import AgentAccessError, run_connection
from secondshift.runs.execution.budgets import Budget, budget_failure
from secondshift.runs.execution.deadlines import run_deadline, timeout_failure
from secondshift.runs.execution.result import AgentResult
from secondshift.runs.execution.subagents import FINISHED, finish
from secondshift.runs.models import Run, RunFailure
from secondshift.runs.reporting.failures import StageFailure

FAILURE_SUMMARIES = {
    AgentOutcome.QUOTA_EXHAUSTED: "Subscription usage exhausted. Wait for your provider's allowance to reset, then start a new run.",
    AgentOutcome.RECONNECT_REQUIRED: "Agent sign-in expired. Reconnect the subscription on Models, then start a new run.",
    AgentOutcome.SUBAGENT_REFUSED: "The agent ran a sub-agent this run does not allow, so it was stopped.",
    AgentOutcome.AGENT_STOPPED: "The agent's sandbox stopped before it finished, for example because it ran out of memory or was stopped. Start a new run.",
}
FAILURE_KINDS = {
    AgentOutcome.QUOTA_EXHAUSTED: RunFailure.MODEL_PROVIDER,
    AgentOutcome.RECONNECT_REQUIRED: RunFailure.MODEL_PROVIDER,
    AgentOutcome.AGENT_STOPPED: RunFailure.SANDBOX,
}
DEFAULT_FAILURE = "The agent could not finish. Check its login and subscription usage before starting a new run."


def confirm_sign_in(run: Run, connection: AgentConnection) -> str:
    """Check the login before saying its sign-in expired.

    The run's reason comes from words in the CLI's last error, and a tool's
    error can contain them too. Only a real check marks the login refused,
    which stops its runs. If the check signs in, the run failed for another
    reason. The run's lock on the login has ended, so the check can take it.
    """
    try:
        check = check_agent_connection(connection).last_check or {}
    except AgentConnectionError:
        # The check could not run. Keep the run's own reason.
        return AgentOutcome.RECONNECT_REQUIRED
    add_event(
        run.pk,
        "Checked the agent login after the run.",
        metadata={"reason": check.get("reason", "")},
    )
    found = check.get("reason")
    if found == AgentOutcome.COMPLETED:
        return AgentOutcome.AGENT_FAILED
    if found == AgentOutcome.QUOTA_EXHAUSTED:
        return AgentOutcome.QUOTA_EXHAUSTED
    # Refused, or a check that did not finish and so proves nothing.
    return AgentOutcome.RECONNECT_REQUIRED


def outcome_failure(run: Run, outcome: str) -> StageFailure:
    """The same failure the built-in agent records for the same stop."""
    if outcome == AgentOutcome.TIMED_OUT:
        return timeout_failure(run)
    limits = run.plan.agent.limits
    if outcome == AgentOutcome.TOKEN_BUDGET_EXHAUSTED:
        return budget_failure(Budget.TOKENS, limits.token_budget)
    if outcome == AgentOutcome.TURN_BUDGET_EXHAUSTED:
        return budget_failure(Budget.REQUESTS, limits.max_turns)
    summary = FAILURE_SUMMARIES.get(outcome, DEFAULT_FAILURE)
    return StageFailure(FAILURE_KINDS.get(outcome, RunFailure.SKILL), summary, summary)


def record_subagents(run: Run, subagents: list[dict[str, int]]) -> None:
    """One timeline event for each sub-agent, as the built-in agent records.

    The CLI reports only counts, after it ends, so there is no start event.
    """
    for number, used in enumerate(subagents, start=1):
        finish(
            run.pk,
            f"cli-{number}",
            number,
            FINISHED if used.get("finished") else "stopped",
            int(used.get("requests", 0)),
            int(used.get("tool_calls", 0)),
        )


@DBOS.step(name="secondshift.execute_cli_agent")
def execute_cli_step(run_id: int, session_id: str, prompt: str) -> dict[str, Any]:
    run = get_run(run_id, "job")
    # A lease means an earlier attempt started the agent before the worker
    # stopped. It may already have changed external services.
    if run_has_lease(run):
        raise RuntimeError(
            "The agent was interrupted. It is not started again, because it may "
            "already have changed external services. Start a new run."
        )
    try:
        connection = run_connection(run)
    except AgentAccessError as error:
        # Run summaries show RuntimeError messages. This one names the fix.
        raise RuntimeError(str(error)) from None
    remaining = int((run_deadline(run) - timezone.now()).total_seconds())
    if remaining < 1:
        raise RuntimeError("The run reached its time limit.")
    token = secrets.token_urlsafe(40)
    lease = create_lease(run, hashlib.sha256(token.encode()).hexdigest(), session_id)
    limits = run.plan.agent.limits
    try:
        client = get_runner_client()
        result = client.run_agent(
            session_id,
            AgentInput(
                backend=connection.backend,
                credential_key=connection.credential_key.hex,
                auth=client.encrypted_auth(connection.auth_data),
                broker_token=token,
                prompt=prompt,
                max_turns=limits.max_turns,
                token_budget=limits.token_budget,
                timeout_seconds=remaining,
                subagents=run.plan.agent.subagents,
            ),
        )
    finally:
        end_lease(lease.pk)
    report = result.report.strip()
    succeeded = result.succeeded and bool(report)
    reason = result.reason
    if reason == AgentOutcome.RECONNECT_REQUIRED:
        reason = confirm_sign_in(run, connection)
    record_subagents(run, result.subagents)
    add_event(
        run.pk,
        "Agent execution finished.",
        metadata={
            "backend": connection.backend,
            "reason": reason,
            "versions": result.versions,
            "usage": result.usage,
        },
    )
    details = {"backend": connection.backend, "reason": reason, "usage": result.usage}
    if succeeded:
        agent_result = AgentResult.success(run, report, details)
    else:
        agent_result = AgentResult.stopped(
            run, outcome_failure(run, reason), details=details
        )
    return agent_result.model_dump(mode="json")
