"""Resolve workspace execution and freeze it into each new run plan."""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from secondshift.config.constants import AgentBackend, ExecutionMode
from secondshift.connections.agents import usable_agent_connection
from secondshift.connections.models import AgentConnection, Connection
from secondshift.jobs.models import Job
from secondshift.workspaces.models import Workspace


@dataclass(frozen=True)
class ExecutionSelection:
    key: str
    label: str
    backend: str
    source: str
    connection: AgentConnection | None = None
    model: Connection | None = None
    problem: str = ""

    @property
    def ready(self) -> bool:
        return not self.problem and (
            self.connection is not None or self.model is not None
        )


def cli_enabled() -> bool:
    return bool(settings.AGENT_IMAGE and settings.AGENT_BROKER_URL)


def default_model_connection(workspace: Workspace) -> Connection | None:
    """The connected model provider marked as the workspace default."""
    return Connection.objects.filter(
        workspace=workspace,
        kind=Connection.Kind.MODEL,
        is_default=True,
        status=Connection.Status.CONNECTED,
    ).first()


def model_problem(model: Connection | None) -> str:
    """Why runs cannot use this default model provider, or empty when they can."""
    if model is None:
        return "No default model provider is connected."
    if not model.default_model.strip():
        return f"The default model provider {model.display_name} has no model selected."
    if (model.metadata.get("last_check") or {}).get("ok") is False:
        return f"The default model provider {model.display_name} failed its last check."
    return ""


def workspace_model(workspace: Workspace) -> Connection | None:
    """The default model provider, if runs can use it."""
    model = default_model_connection(workspace)
    return None if model_problem(model) else model


def model_selection(workspace: Workspace, source: str) -> ExecutionSelection:
    connection = default_model_connection(workspace)
    problem = model_problem(connection)
    model = None if problem else connection
    return ExecutionSelection(
        key=ExecutionMode.MODEL,
        label=f"Model API · {model.display_name} · {model.default_model}"
        if model
        else "API model unavailable",
        backend=AgentBackend.PYDANTIC,
        source=source,
        model=model,
        problem=problem,
    )


def subscription_problem(connection: AgentConnection, workspace: Workspace) -> str:
    """Why runs cannot use this agent login, or empty when they can."""
    if not cli_enabled():
        return "Agent subscriptions are not set up on this server."
    if not connection.connected:
        return f"The agent login {connection.display_name} is disconnected. Reconnect it on Connections."
    if not usable_agent_connection(connection, workspace.pk):
        return f"The agent login {connection.display_name} belongs to someone who is no longer a workspace owner."
    return ""


def subscription_selection(
    connection: AgentConnection, workspace: Workspace, source: str
) -> ExecutionSelection:
    return ExecutionSelection(
        key=f"agent:{connection.pk}",
        label=f"{'Codex' if connection.backend == AgentBackend.CODEX else 'Claude Code'} subscription · {connection.display_name}",
        backend=connection.backend,
        source=source,
        connection=connection,
        problem=subscription_problem(connection, workspace),
    )


def workspace_execution(workspace: Workspace) -> ExecutionSelection:
    # An explicit choice never falls through to a different provider or billing path.
    if workspace.default_agent_connection_id:
        return subscription_selection(
            workspace.default_agent_connection, workspace, "workspace"
        )
    if workspace.agent_mode == ExecutionMode.MODEL:
        return model_selection(workspace, "workspace")
    model = model_selection(workspace, "automatic")
    if model.ready:
        return model
    connections = (
        [
            connection
            for connection in AgentConnection.objects.filter(
                workspace=workspace, connected=True
            )
            if usable_agent_connection(connection, workspace.pk)
        ]
        if cli_enabled()
        else []
    )
    if len(connections) == 1:
        return subscription_selection(connections[0], workspace, "automatic")
    if not connections and model.problem != model_problem(None):
        # A default model exists but cannot run. Say what is wrong with it.
        return model
    problem = (
        "Multiple subscriptions are connected. Choose a default execution agent in Settings."
        if connections
        else "Connect a model provider or agent subscription on Connections."
    )
    return ExecutionSelection(
        key=ExecutionMode.AUTO,
        label="No execution agent selected",
        backend=AgentBackend.PYDANTIC,
        source="automatic",
        problem=problem,
    )


def job_execution(job: Job) -> ExecutionSelection:
    return workspace_execution(job.workspace)


def selection_key(workspace: Workspace) -> str:
    if workspace.default_agent_connection_id:
        return f"agent:{workspace.default_agent_connection_id}"
    return (
        ExecutionMode.MODEL
        if workspace.agent_mode == ExecutionMode.MODEL
        else ExecutionMode.AUTO
    )


def available_choices(workspace: Workspace, user: User) -> list[dict[str, Any]]:
    choices = []
    model = model_selection(workspace, "workspace")
    if model.ready:
        choices.append({"key": model.key, "label": model.label, "disabled": False})
    for connection in AgentConnection.objects.filter(
        workspace=workspace, owner=user, connected=True
    ):
        selection = subscription_selection(connection, workspace, "workspace")
        if selection.ready:
            choices.append(
                {"key": selection.key, "label": selection.label, "disabled": False}
            )
    return choices


def execution_configuration(workspace: Workspace, user: User) -> dict[str, Any]:
    effective = workspace_execution(workspace)
    key = selection_key(workspace)
    choices = available_choices(workspace, user)
    choices.insert(
        0,
        {
            "key": ExecutionMode.AUTO,
            "label": f"Automatic · {effective.label}"
            if key == ExecutionMode.AUTO
            else "Automatic · use configured access",
            "disabled": False,
        },
    )
    if key not in {item["key"] for item in choices}:
        choices.append(
            {
                "key": key,
                "label": f"{effective.label} · unavailable for selection",
                "disabled": True,
            }
        )
    return {
        "enabled": cli_enabled(),
        "choices": choices,
        "selection_key": key,
        "effective_key": effective.key,
        "effective_label": effective.label,
        "effective_backend": effective.backend,
        "ready": effective.ready,
        "problem": effective.problem,
    }


def save_execution_preference(workspace: Workspace, key: str, user: User) -> None:
    choices = available_choices(workspace, user)
    automatic = ExecutionMode.AUTO
    if key != automatic and key not in {item["key"] for item in choices}:
        raise ValidationError(
            "Choose a working model connection or your own connected subscription."
        )
    connection = (
        AgentConnection.objects.get(
            pk=int(key.split(":", 1)[1]), workspace=workspace, owner=user
        )
        if key.startswith("agent:")
        else None
    )
    workspace.agent_mode = (
        ExecutionMode.SUBSCRIPTION
        if connection
        else ExecutionMode.MODEL
        if key == ExecutionMode.MODEL
        else automatic
    )
    workspace.default_agent_connection = connection
    workspace.save(
        update_fields=["agent_mode", "default_agent_connection", "updated_at"]
    )
