"""What runs a workspace's jobs: one model key or one agent login."""

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from secondshift.config.constants import AgentBackend
from secondshift.connections.agents import (
    product_name,
    sign_in_refused,
    usable_agent_connection,
)
from secondshift.connections.dao import (
    connected_logins,
    has_models_or_logins,
    login_run_counts,
    usable_models,
)
from secondshift.connections.models import AgentConnection, Connection
from secondshift.workspaces.models import Workspace

# Choice keys name one model key or one login, for example "model:3" or "agent:7".
MODEL_PREFIX = "model:"
AGENT_PREFIX = "agent:"
NOTHING_CHOSEN = "Choose a model API key or a subscription to run jobs."
# A login's runs per day count over this rolling window.
RUN_CAP_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class ExecutionSelection:
    key: str
    label: str
    backend: AgentBackend
    connection: AgentConnection | None = None
    model: Connection | None = None
    problem: str = ""

    @property
    def ready(self) -> bool:
        return not self.problem and (
            self.connection is not None or self.model is not None
        )


def recent_login_runs(workspace_id: int) -> Counter[int]:
    """Runs per agent login in the cap window. Only ready plans used a login."""
    return login_run_counts(workspace_id, timezone.now() - RUN_CAP_WINDOW)


def cli_enabled() -> bool:
    return bool(settings.AGENT_IMAGE and settings.AGENT_BROKER_URL)


def model_problem(model: Connection) -> str:
    """Why runs cannot use this model key, or empty when they can."""
    if model.status != Connection.Status.CONNECTED:
        return f"The model provider {model.display_name} is disconnected. Reconnect it."
    if not model.default_model.strip():
        return f"The model provider {model.display_name} has no model selected."
    if model.failed_last_check:
        return f"The model provider {model.display_name} failed its last check."
    return ""


def workspace_model(workspace: Workspace) -> Connection | None:
    """The chosen model provider, if runs can use it."""
    model = workspace.default_model_connection
    return None if model is None or model_problem(model) else model


def model_selection(connection: Connection) -> ExecutionSelection:
    problem = model_problem(connection)
    return ExecutionSelection(
        key=f"{MODEL_PREFIX}{connection.pk}",
        label=f"Model provider · {connection.display_name}"
        + ("" if problem else f" · {connection.default_model}"),
        backend=AgentBackend.PYDANTIC,
        model=None if problem else connection,
        problem=problem,
    )


def subscription_problem(connection: AgentConnection, workspace: Workspace) -> str:
    """Why runs cannot use this agent login, or empty when they can."""
    if not cli_enabled():
        return "Agent subscriptions are not set up on this server."
    if not connection.connected:
        return (
            f"The agent login {connection.display_name} is disconnected. Reconnect it."
        )
    if sign_in_refused(connection):
        return f"The agent login {connection.display_name} was refused at its last check. Reconnect it."
    if not usable_agent_connection(connection, workspace.pk):
        return f"The agent login {connection.display_name} belongs to someone who is no longer a workspace owner."
    cap = connection.max_runs_per_day
    if cap is not None and recent_login_runs(workspace.pk)[connection.pk] >= cap:
        return (
            f"The agent login {connection.display_name} reached its limit of "
            f"{cap} runs in 24 hours. Raise its limit or wait."
        )
    return ""


def subscription_selection(
    connection: AgentConnection, workspace: Workspace
) -> ExecutionSelection:
    backend = AgentBackend(connection.backend)
    product = product_name(backend)
    return ExecutionSelection(
        key=f"{AGENT_PREFIX}{connection.pk}",
        label=f"{product} subscription · {connection.display_name}",
        backend=backend,
        connection=connection,
        problem=subscription_problem(connection, workspace),
    )


def workspace_execution(workspace: Workspace) -> ExecutionSelection:
    """What runs the workspace's jobs. It changes only when an owner changes it."""
    if workspace.default_agent_connection_id:
        return subscription_selection(workspace.default_agent_connection, workspace)
    if workspace.default_model_connection_id:
        return model_selection(workspace.default_model_connection)
    return ExecutionSelection(
        key="",
        label="Nothing chosen",
        backend=AgentBackend.PYDANTIC,
        problem=NOTHING_CHOSEN,
    )


def execution_choices(workspace: Workspace, user: User) -> list[ExecutionSelection]:
    """The workspace's model keys, then the user's own logins."""
    models = usable_models(workspace)
    logins = connected_logins(workspace, user)
    return [
        *(model_selection(model) for model in models),
        *(subscription_selection(login, workspace) for login in logins),
    ]


def execution_configuration(workspace: Workspace, user: User) -> dict[str, Any]:
    selected = workspace_execution(workspace)
    choices = [
        {
            "key": choice.key,
            "label": choice.label,
            "disabled": not choice.ready,
            "problem": choice.problem,
        }
        for choice in execution_choices(workspace, user)
    ]
    if selected.key and selected.key not in {choice["key"] for choice in choices}:
        # Another owner's login or a disconnected provider. Shown, not selectable.
        choices.append(
            {
                "key": selected.key,
                "label": selected.label,
                "disabled": True,
                "problem": selected.problem,
            }
        )
    return {
        "enabled": cli_enabled(),
        "choices": choices,
        "selected": selected.key,
        "label": selected.label,
        "backend": selected.backend,
        "ready": selected.ready,
        "problem": selected.problem,
    }


def execution_status(workspace: Workspace) -> dict[str, Any]:
    """What runs the jobs, why runs would fail, and where to fix it."""
    selected = workspace_execution(workspace)
    nothing_chosen = not selected.key
    return {
        "label": selected.label,
        "backend": selected.backend,
        "problem": selected.problem,
        "chosen": not nothing_chosen,
        "needs_setup": nothing_chosen and not has_models_or_logins(workspace.pk),
        # The chosen model provider, so the page can open it ready to fix.
        "fix_connection_id": workspace.default_model_connection_id
        if selected.problem and not nothing_chosen and selected.backend.uses_model_api
        else None,
    }


def choose_execution(
    workspace: Workspace,
    model: Connection | None = None,
    login: AgentConnection | None = None,
) -> None:
    """Point the workspace at one model key or one login. None of both clears it."""
    workspace.default_model_connection = model
    workspace.default_agent_connection = login
    workspace.save(
        update_fields=[
            "default_model_connection",
            "default_agent_connection",
            "updated_at",
        ]
    )


def save_execution_preference(workspace: Workspace, key: str, user: User) -> None:
    choice = next(
        (
            choice
            for choice in execution_choices(workspace, user)
            if choice.key == key and choice.ready
        ),
        None,
    )
    if choice is None:
        raise ValidationError(
            "Choose a working model provider or one of your own working logins."
        )
    choose_execution(workspace, model=choice.model, login=choice.connection)


def use_when_nothing_runs(
    workspace: Workspace,
    model: Connection | None = None,
    login: AgentConnection | None = None,
) -> None:
    """A new provider or login runs the jobs when nothing chosen can.

    A broken newcomer only fills an empty choice, so it never hides a working
    fix behind another problem. Settings shows the result.
    """
    current = workspace_execution(workspace)
    if current.ready:
        return
    candidate = model_selection(model) if model else None
    if current.key and candidate is not None and not candidate.ready:
        return
    choose_execution(workspace, model=model, login=login)


def apply_model_default(connection: Connection, is_default: bool | None) -> None:
    """The model provider form's default switch.

    None means the form did not say, so the provider is used only when nothing
    chosen can run jobs.
    """
    workspace = connection.workspace
    if is_default:
        choose_execution(workspace, model=connection)
    elif is_default is None:
        use_when_nothing_runs(workspace, model=connection)
    elif workspace.default_model_connection_id == connection.pk:
        choose_execution(workspace)
