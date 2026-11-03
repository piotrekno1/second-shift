"""Person-owned CLI logins; only references enter job plans."""

import json
import uuid

from django.conf import settings
from django.contrib.auth.models import User

from secondshift.config.constants import AgentBackend
from secondshift.connections.models import AgentConnection
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
