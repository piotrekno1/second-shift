"""The model a subscription login's runs use. Only its owner chooses it."""

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client

from secondshift.connections import agents as agent_logins
from secondshift.connections.execution import choose_execution, workspace_execution
from secondshift.connections.models import AgentConnection
from secondshift.runner.tests.fakes import agent_runner
from secondshift.workspaces.models import Workspace, WorkspaceMembership

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def runner_keys(settings: Any, tmp_path: Any) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"


def choose_model(client: Client, user: User, login: AgentConnection, model: str) -> Any:
    client.force_login(user)
    return client.post(
        f"/api/agent-connections/{login.pk}/model",
        {"model": model},
        content_type="application/json",
    )


def test_the_owner_chooses_a_model_and_runs_name_it(
    agent_login: tuple[Workspace, AgentConnection, User], client: Client
) -> None:
    workspace, login, owner = agent_login
    choose_execution(workspace, login=login)

    response = choose_model(client, owner, login, " opus ")

    assert response.status_code == 200
    assert response.json()["default_model"] == "opus"
    selection = workspace_execution(Workspace.objects.get(pk=workspace.pk))
    assert selection.label == "Claude Code subscription · Personal · opus"

    assert choose_model(client, owner, login, "").json()["default_model"] == ""


@pytest.mark.parametrize(
    "model", ["--dangerously-skip-permissions", "opus --tools Bash", "<synthetic>"]
)
def test_a_model_name_that_could_be_an_option_is_refused(
    agent_login: tuple[Workspace, AgentConnection, User], client: Client, model: str
) -> None:
    _, login, owner = agent_login

    response = choose_model(client, owner, login, model)

    assert response.status_code == 422
    assert AgentConnection.objects.get(pk=login.pk).default_model == ""


def test_only_the_logins_owner_chooses_its_model(
    agent_login: tuple[Workspace, AgentConnection, User], client: Client
) -> None:
    workspace, login, _ = agent_login
    other = get_user_model().objects.create_user(username="other")
    WorkspaceMembership.objects.create(workspace=workspace, user=other, role="owner")
    admin = get_user_model().objects.create_user(username="admin")
    WorkspaceMembership.objects.create(workspace=workspace, user=admin, role="admin")

    assert choose_model(client, other, login, "opus").status_code == 404
    assert choose_model(client, admin, login, "opus").status_code == 403
    assert AgentConnection.objects.get(pk=login.pk).default_model == ""


def test_a_check_tries_the_chosen_model(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, login, owner = agent_login
    runner, sent = agent_runner({"reason": "completed", "succeeded": True})
    monkeypatch.setattr(agent_logins, "runner_client", lambda target: runner)
    choose_model(client, owner, login, "opus")

    last = client.post(f"/api/agent-connections/{login.pk}/check").json()["last_check"]

    assert sent[-1]["json"]["model"] == "opus"
    assert last["message"] == "Signed in. opus answered a test message."


def test_a_plan_without_the_model_fails_the_check_but_keeps_the_login(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, login, owner = agent_login
    choose_execution(workspace, login=login)
    runner, _ = agent_runner({"reason": "model_unavailable", "succeeded": False})
    monkeypatch.setattr(agent_logins, "runner_client", lambda target: runner)
    choose_model(client, owner, login, "opus")

    last = client.post(f"/api/agent-connections/{login.pk}/check").json()["last_check"]

    assert last["ok"] is False
    assert last["message"] == (
        "Signed in, but this plan cannot use opus. Choose another model, "
        "or leave it empty for Claude Code's default."
    )
    # Only a refused sign-in stops the login's runs.
    assert not workspace_execution(Workspace.objects.get(pk=workspace.pk)).problem


@pytest.mark.parametrize(("ok", "kept"), [(True, False), (False, True)])
def test_a_new_model_drops_a_passed_check_but_not_a_refused_sign_in(
    agent_login: tuple[Workspace, AgentConnection, User],
    client: Client,
    ok: bool,
    kept: bool,
) -> None:
    _, login, owner = agent_login
    login.last_check = {"ok": ok, "reason": "x", "message": "", "checked_at": ""}
    login.save()

    choose_model(client, owner, login, "opus")

    assert (AgentConnection.objects.get(pk=login.pk).last_check is not None) is kept
