"""Resolve workspace execution and freeze it into each new run plan."""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from secondshift.config.constants import AgentBackend
from secondshift.connections.agents import usable_agent_connection
from secondshift.connections.models import AgentConnection, Connection
from secondshift.jobs.models import Job
from secondshift.runs.reporting.connection_usage import agent_login_runs
from secondshift.workspaces.models import Workspace

# The choice key for the default model provider. Logins use "agent:<id>".
MODEL_CHOICE = "model"


@dataclass(frozen=True)
class ExecutionSelection:
    key: str
    label: str
    backend: str
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


def model_selection(workspace: Workspace) -> ExecutionSelection:
    connection = default_model_connection(workspace)
    problem = model_problem(connection)
    return ExecutionSelection(
        key=MODEL_CHOICE,
        label=f"Model provider · {connection.display_name} · {connection.default_model}"
        if connection and not problem
        else "Model provider",
        backend=AgentBackend.PYDANTIC,
        model=None if problem else connection,
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
    cap = connection.max_runs_per_day
    if cap is not None and agent_login_runs(workspace.pk)[connection.pk] >= cap:
        return (
            f"The agent login {connection.display_name} reached its limit of "
            f"{cap} runs in 24 hours. Raise the limit on Connections or wait."
        )
    return ""


def subscription_selection(
    connection: AgentConnection, workspace: Workspace
) -> ExecutionSelection:
    product = AgentBackend(connection.backend).name.replace("_", " ").title()
    return ExecutionSelection(
        key=f"agent:{connection.pk}",
        label=f"{product} subscription · {connection.display_name}",
        backend=connection.backend,
        connection=connection,
        problem=subscription_problem(connection, workspace),
    )


def workspace_execution(workspace: Workspace) -> ExecutionSelection:
    """What runs the workspace's jobs. It changes only when an owner changes it."""
    if workspace.default_agent_connection_id:
        return subscription_selection(workspace.default_agent_connection, workspace)
    return model_selection(workspace)


def job_execution(job: Job) -> ExecutionSelection:
    return workspace_execution(job.workspace)


def execution_choices(workspace: Workspace, user: User) -> list[ExecutionSelection]:
    """The model provider, then the user's own logins."""
    logins = AgentConnection.objects.filter(
        workspace=workspace, owner=user, connected=True
    )
    return [
        model_selection(workspace),
        *(subscription_selection(login, workspace) for login in logins),
    ]


def execution_configuration(workspace: Workspace, user: User) -> dict[str, Any]:
    selected = workspace_execution(workspace)
    choices = [
        {"key": choice.key, "label": choice.label, "disabled": not choice.ready}
        for choice in execution_choices(workspace, user)
    ]
    # The model provider stays selectable while broken, so owners can move back.
    choices[0]["disabled"] = False
    if selected.key not in {choice["key"] for choice in choices}:
        # Another owner's login. Shown, but only its owner can select it again.
        choices.append({"key": selected.key, "label": selected.label, "disabled": True})
    return {
        "enabled": cli_enabled(),
        "choices": choices,
        "selected": selected.key,
        "label": selected.label,
        "backend": selected.backend,
        "ready": selected.ready,
        "problem": selected.problem,
    }


def save_execution_preference(workspace: Workspace, key: str, user: User) -> None:
    connection = None
    if key != MODEL_CHOICE:
        choice = next(
            (
                choice
                for choice in execution_choices(workspace, user)
                if choice.key == key and choice.ready
            ),
            None,
        )
        if choice is None or choice.connection is None:
            raise ValidationError(
                "Choose the model provider or one of your own working logins."
            )
        connection = choice.connection
    workspace.default_agent_connection = connection
    workspace.save(update_fields=["default_agent_connection", "updated_at"])


def use_login_when_no_model_works(connection: AgentConnection) -> None:
    """A first login runs the jobs when nothing else can. Settings shows it."""
    workspace = connection.workspace
    if workspace.default_agent_connection_id or workspace_model(workspace):
        return
    workspace.default_agent_connection = connection
    workspace.save(update_fields=["default_agent_connection", "updated_at"])
