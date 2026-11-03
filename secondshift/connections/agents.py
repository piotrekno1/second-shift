"""Person-owned CLI logins; only references enter job plans."""

import json
import uuid
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from secondshift.config.constants import AgentBackend
from secondshift.connections.models import AgentConnection
from secondshift.runner.agent_credentials import encrypt_agent_auth
from secondshift.runner.client import get_runner_client
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.workspaces.models import Workspace, WorkspaceMembership


class AgentConnectionError(ValueError):
    pass


class RunnerCleanupPending(RuntimeError):
    """The login is blocked, but the runner still holds its cached files."""


def validate_agent_auth(backend: str, value: str) -> str:
    if backend == AgentBackend.CLAUDE_CODE:
        if not value.startswith("sk-ant-oat") or any(c.isspace() for c in value):
            raise AgentConnectionError(
                "Use a Claude Code subscription token from claude setup-token."
            )
        return value
    if backend != AgentBackend.CODEX:
        raise AgentConnectionError("Choose Claude Code or Codex.")
    try:
        auth = json.loads(value)
        tokens = auth["tokens"]
        if not isinstance(tokens, dict) or not all(
            isinstance(tokens.get(key), str) and tokens[key]
            for key in ("access_token", "refresh_token", "id_token", "account_id")
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise AgentConnectionError(
            "Use the auth.json from a Codex ChatGPT login."
        ) from None
    # Never import configuration, endpoints, API keys, hooks, or MCP servers.
    return json.dumps(
        {
            "tokens": {
                key: tokens[key]
                for key in ("access_token", "refresh_token", "id_token", "account_id")
            },
            "last_refresh": auth.get("last_refresh"),
        }
    )


def save_agent_connection(
    workspace: Workspace,
    owner: User,
    backend: str,
    display_name: str,
    auth: str,
    connection: AgentConnection | None = None,
) -> AgentConnection:
    if not settings.AGENT_IMAGE or not settings.AGENT_BROKER_URL:
        raise AgentConnectionError("CLI execution is not configured on this server.")
    cleaned = validate_agent_auth(backend, auth.strip())
    if connection is None:
        connection = AgentConnection(workspace=workspace, owner=owner, backend=backend)
    if connection.workspace_id != workspace.pk or connection.owner_id != owner.pk:
        raise AgentConnectionError("Only the login's owner can reconnect it.")
    if connection.backend != backend:
        raise AgentConnectionError("The agent backend cannot change during reconnect.")
    connection.display_name = (
        display_name.strip() or AgentBackend(backend).name.replace("_", " ").title()
    )
    connection.auth_data = cleaned
    connection.credential_key = uuid.uuid4()
    connection.connected = True
    # A check described the old login.
    connection.last_check = None
    connection.full_clean()
    connection.save()
    return connection


def reconnect_agent_connection(
    connection: AgentConnection,
    owner: User,
    backend: str,
    display_name: str,
    auth: str,
) -> AgentConnection:
    validate_agent_auth(backend, auth.strip())
    if backend != connection.backend:
        raise AgentConnectionError("The agent backend cannot change during reconnect.")
    # Block the old login before its runner cache is removed.
    connection.connected = False
    connection.save(update_fields=["connected", "updated_at"])
    remove_runner_credentials(connection)
    return save_agent_connection(
        connection.workspace, owner, backend, display_name, auth, connection
    )


def disconnect_agent_connection(connection: AgentConnection) -> None:
    connection.connected = False
    connection.auth_data = ""
    connection.save(update_fields=["connected", "auth_data", "updated_at"])
    remove_runner_credentials(connection)


def remove_runner_credentials(connection: AgentConnection) -> None:
    try:
        response = get_runner_client().send(
            "DELETE", f"/v1/agent-credentials/{connection.credential_key.hex}"
        )
        if response.status_code != 204:
            raise RunnerProtocolError("Credential cleanup failed.")
    except RunnerProtocolError:
        raise RunnerCleanupPending(
            "Login disconnected. The runner still holds a cached copy. "
            "Disconnect again when the runner is available."
        ) from None


# What a check's outcome means for the login. Only "completed" and a used-up
# allowance prove the sign-in works.
CHECK_RESULTS: dict[str, tuple[bool, str]] = {
    "completed": (True, "Signed in. {product} answered a test message."),
    "quota_exhausted": (
        True,
        "Signed in, but the plan's usage allowance is used up for now.",
    ),
    "reconnect_required": (
        False,
        "{product} refused the sign-in. Get a new login and reconnect.",
    ),
    "timed_out": (False, "{product} did not answer in time. Check again later."),
}
CHECK_FAILED = (False, "{product} could not finish the check. Check again later.")


def product_name(backend: str) -> str:
    return AgentBackend(backend).name.replace("_", " ").title()


def check_agent_connection(connection: AgentConnection) -> AgentConnection:
    """Send the login one fixed test message through the runner and keep the result.

    It uses a little of the plan's allowance. The runner returns only a reason,
    never the CLI's text, so nothing from the provider is stored.
    """
    if not connection.connected:
        raise AgentConnectionError("Reconnect this login before checking it.")
    try:
        client = get_runner_client()
        key = client.response_payload(client.send("GET", "/v1/agent-provisioning-key"))
        response = client.send(
            "POST",
            "/v1/agent-checks",
            # The runner stops the CLI after its own limit. This covers startup.
            timeout=28,
            json={
                "backend": connection.backend,
                "credential_key": connection.credential_key.hex,
                "auth": encrypt_agent_auth(
                    str(key["public_key"]), connection.auth_data
                ),
            },
        )
        result: dict[str, Any] = client.response_payload(response)
    except RunnerProtocolError:
        raise AgentConnectionError(
            "The runner could not check the login now. It may be running a job "
            "with it. Try again in a moment."
        ) from None
    reason = str(result.get("reason", ""))
    ok, message = CHECK_RESULTS.get(reason, CHECK_FAILED)
    connection.last_check = {
        "ok": ok,
        "reason": reason if reason in CHECK_RESULTS else "agent_failed",
        "message": message.format(product=product_name(connection.backend)),
        "checked_at": timezone.now().isoformat(),
    }
    connection.save(update_fields=["last_check", "updated_at"])
    return connection


def sign_in_refused(connection: AgentConnection) -> bool:
    """The last check proved the login no longer works."""
    return (connection.last_check or {}).get("reason") == "reconnect_required"


def usable_agent_connection(connection: AgentConnection, workspace_id: int) -> bool:
    return bool(
        connection.connected
        and connection.workspace_id == workspace_id
        and connection.backend in (AgentBackend.CLAUDE_CODE, AgentBackend.CODEX)
        and WorkspaceMembership.objects.filter(
            workspace_id=workspace_id,
            user_id=connection.owner_id,
            role=WorkspaceMembership.Role.OWNER,
        ).exists()
    )
