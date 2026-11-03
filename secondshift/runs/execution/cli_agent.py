"""Run a subscription job through the agent CLI, in one step that never repeats."""

import hashlib
import secrets
from typing import Any

from dbos import DBOS
from django.utils import timezone

from secondshift.config.constants import SUMMARY_MAX_CHARS
from secondshift.runner.agent_credentials import encrypt_agent_auth
from secondshift.runner.client import get_runner_client
from secondshift.runs.artifacts import store_run_text
from secondshift.runs.execution.agent_broker import AgentAccessError, run_connection
from secondshift.runs.execution.deadlines import run_deadline
from secondshift.runs.models import AgentLease, Run, RunEvent

FAILURE_SUMMARIES = {
    "quota_exhausted": "Subscription usage exhausted. Wait for your provider's allowance to reset, then start a new run.",
    "reconnect_required": "Agent sign-in expired. Reconnect the subscription on Connections, then start a new run.",
    "timed_out": "The agent reached this run's time limit.",
    "token_budget_exhausted": "The agent reported usage at or above this run's token budget.",
    "turn_budget_exhausted": "The agent reached this run's maximum turns.",
}
DEFAULT_FAILURE = "The agent could not finish. Check its login and subscription usage before starting a new run."


@DBOS.step(name="secondshift.execute_cli_agent")
def execute_cli_step(run_id: int, session_id: str, prompt: str) -> dict[str, Any]:
    run = Run.objects.select_related("job").get(pk=run_id)
    # A lease means an earlier attempt started the agent before the worker
    # stopped. It may already have changed external services.
    if AgentLease.objects.filter(run=run).exists():
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
    lease = AgentLease.objects.create(
        run=run,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        session_id=session_id,
    )
    limits = run.execution_plan["agent"]["limits"]
    try:
        client = get_runner_client()
        provisioning = client.response_payload(
            client.send("GET", "/v1/agent-provisioning-key")
        )
        encrypted_auth = encrypt_agent_auth(
            str(provisioning["public_key"]), connection.auth_data
        )
        response = client.send(
            "POST",
            f"/v1/sessions/{session_id}/agent",
            timeout=remaining + 30,
            json={
                "backend": connection.backend,
                "credential_key": connection.credential_key.hex,
                "auth": encrypted_auth,
                "broker_token": token,
                "prompt": prompt,
                "max_turns": limits["max_turns"],
                "token_budget": limits["token_budget"],
                "timeout_seconds": remaining,
            },
        )
        result = client.response_payload(response)
    finally:
        AgentLease.objects.filter(pk=lease.pk).update(active=False)
    report = str(result.get("report", "")).strip()
    succeeded = bool(result.get("succeeded")) and bool(report)
    reason = str(result.get("reason", "agent_failed"))
    RunEvent.objects.create(
        run=run,
        message="Agent execution finished.",
        metadata={
            "backend": connection.backend,
            "reason": reason,
            "versions": result.get("versions", {}),
            "usage": result.get("usage", {}),
        },
    )
    return {
        "exit_code": 0 if succeeded else 1,
        "succeeded": succeeded,
        "output": store_run_text(run, report).as_dict() if succeeded else None,
        "summary": report[:SUMMARY_MAX_CHARS]
        if succeeded
        else FAILURE_SUMMARIES.get(reason, DEFAULT_FAILURE),
        "details": {
            "backend": connection.backend,
            "reason": reason,
            "usage": result.get("usage", {}),
        },
    }
