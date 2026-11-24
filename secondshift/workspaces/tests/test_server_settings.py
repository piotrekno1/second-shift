import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from secondshift.jobs.tests.fixtures import offer_environments
from secondshift.runner.tests.fakes import sandboxes
from secondshift.runs.models import Runner
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import WorkspaceMembership
from secondshift.workspaces.server_settings import image_label, setting_groups


@pytest.mark.django_db
def test_settings_show_effective_limits_without_secrets(
    client: Client, settings: object
) -> None:
    settings.RUN_TIMEOUT_SECONDS = 900  # type: ignore[attr-defined]
    settings.RUN_RETENTION_DAYS = 0  # type: ignore[attr-defined]
    settings.AGENT_MAX_TURNS = 24  # type: ignore[attr-defined]
    settings.RUNNER_RUNTIME = "runsc"  # type: ignore[attr-defined]
    users = get_user_model().objects
    owner = users.create_user(username="owner", email="owner@example.com")
    teammate = users.create_user(username="teammate", email="team@example.com")
    workspace = create_workspace(name="Team", slug="team", owner=owner)
    WorkspaceMembership.objects.create(workspace=workspace, user=teammate, role="admin")
    client.force_login(owner)

    body = client.get("/api/settings").json()

    assert body["workspace_name"] == "Team"
    values = {
        item["label"]: (item["value"], item["env"])
        for group in body["groups"]
        for item in group["items"]
    }
    assert values["History kept for"][0] == "Forever"
    limits = {item["key"]: item for item in body["limits"]}
    assert limits["timeout_seconds"]["value"] == 900
    assert limits["max_turns"]["value"] == 24
    assert limits["max_turns"]["override"] is None
    assert body["can_edit_limits"] is True
    assert values["Isolation"][0] == "gVisor sandbox"
    assert body["sandbox"] == "gVisor sandbox"
    text = json.dumps(body).lower()
    assert "secret" not in text
    assert "token=" not in text


@pytest.mark.django_db
def test_settings_require_sign_in(client: Client) -> None:
    assert client.get("/api/settings").status_code == 401


def test_image_label_shortens_a_pinned_digest() -> None:
    assert image_label("python:3.13-slim@sha256:" + "a" * 64) == (
        "python:3.13-slim @ aaaaaaaaaaaa"
    )
    assert image_label("registry.example/sandbox:1") == "registry.example/sandbox:1"


@pytest.mark.django_db
def test_only_staff_see_the_server_variables(client: Client) -> None:
    owner = get_user_model().objects.create_user(username="owner", email="o@x.dev")
    create_workspace(name="Team", slug="team", owner=owner)
    client.force_login(owner)

    def variables() -> set[str]:
        body = client.get("/api/settings").json()
        return {item["env"] for group in body["groups"] for item in group["items"]}

    def limit_error() -> str:
        return client.post(
            "/api/settings/limits",
            data=json.dumps({"max_turns": 10_000_000}),
            content_type="application/json",
        ).json()["detail"]

    # An owner on a hosted server cannot set them, so they are not shown.
    assert variables() == {""}
    assert "SECONDSHIFT" not in limit_error()

    owner.is_staff = True
    owner.save(update_fields=["is_staff"])

    assert "SECONDSHIFT_RUN_RETENTION_DAYS" in variables()
    assert limit_error().endswith(
        "Set SECONDSHIFT_AGENT_MAX_TURNS_CEILING to allow more."
    )


@pytest.mark.django_db
def test_background_sync_is_listed_with_local_repositories(settings: object) -> None:
    settings.LOCAL_REPOSITORIES_ENABLED = True  # type: ignore[attr-defined]
    settings.REPOSITORY_SYNC_INTERVAL_SECONDS = 300  # type: ignore[attr-defined]
    values = {item.label: item.value for g in setting_groups() for item in g.items}
    assert values["Background sync"] == "Every 5 minutes"

    settings.REPOSITORY_SYNC_INTERVAL_SECONDS = 0  # type: ignore[attr-defined]
    values = {item.label: item.value for g in setting_groups() for item in g.items}
    assert values["Background sync"] == "Off"

    settings.LOCAL_REPOSITORIES_ENABLED = False  # type: ignore[attr-defined]
    values = {item.label: item.value for g in setting_groups() for item in g.items}
    assert "Background sync" not in values


@pytest.mark.django_db
def test_each_named_environment_is_listed_with_its_env_var() -> None:
    offer_environments(
        sandboxes("python:3.13-slim", node="ghcr.io/org/runner-node@sha256:" + "b" * 64)
    )
    items = {item.label: item for g in setting_groups() for item in g.items}

    default = items["Default environment"]
    assert (default.value, default.env) == ("python", "SECONDSHIFT_SANDBOX_DEFAULT")
    assert items["Environment: python"].env == "SECONDSHIFT_SANDBOX_PYTHON"
    assert "Environment: default" not in items
    node = items["Environment: node"]
    assert node.value == "ghcr.io/org/runner-node @ bbbbbbbbbbbb"
    assert node.env == "SECONDSHIFT_SANDBOX_NODE"
    assert "environment: node" in node.help


@pytest.mark.django_db
def test_runners_that_offer_the_same_are_listed_once() -> None:
    offer_environments(sandboxes("python:3.13-slim"), name="runner-a")
    offer_environments(sandboxes("python:3.13-slim"), name="runner-b")
    labels = [item.label for g in setting_groups() for item in g.items]

    assert labels.count("Environment: python") == 1


@pytest.mark.django_db
def test_runners_that_differ_are_listed_each_by_name() -> None:
    offer_environments(sandboxes("python:3.13-slim"), name="runner-a")
    offer_environments(sandboxes("python:3.14-slim", node="node:22"), name="runner-b")
    # One that has not answered yet is left out.
    offer_environments(sandboxes("other"), name="runner-c")
    Runner.objects.filter(name="runner-c").update(images=None)
    items = {item.label: item.value for g in setting_groups() for item in g.items}

    assert items["Environment: python on runner-a"] == "python:3.13-slim"
    assert items["Environment: python on runner-b"] == "python:3.14-slim"
    assert items["Environment: node on runner-b"] == "node:22"
    assert items["Default environment on runner-a"] == "python"
    assert not any("runner-c" in label for label in items)


@pytest.mark.django_db
def test_the_page_says_when_no_runner_has_listed_environments() -> None:
    items = {item.label: item for g in setting_groups() for item in g.items}

    assert items["Environments"].value == "No runner has answered"
    assert "Environment: default" not in items


@pytest.mark.django_db
def test_only_staff_see_the_runners(client: Client) -> None:
    owner = get_user_model().objects.create_user(username="owner", email="o@x.dev")
    create_workspace(name="Team", slug="team", owner=owner)
    offer_environments(sandboxes("python:3.13-slim"), name="runner-a")
    Runner.objects.filter(name="runner-a").update(agents=False, max_sessions=4)
    offer_environments(sandboxes("python:3.13-slim"), name="runner-b")
    Runner.objects.filter(name="runner-b").update(
        state=Runner.State.DRAINING,
        failing_since=timezone.now() - timedelta(minutes=15),
    )
    client.force_login(owner)

    def runners() -> dict[str, dict[str, str]]:
        body = client.get("/api/settings").json()
        groups = {group["title"]: group["items"] for group in body["groups"]}
        return {item["label"]: item for item in groups.get("Runners", [])}

    assert runners() == {}

    owner.is_staff = True
    owner.save(update_fields=["is_staff"])
    shown = runners()

    assert shown["runner-a"]["value"] == "Active · answering"
    assert shown["runner-a"]["help"].startswith("0 runs of 4 at once.")
    assert "Environments: python." in shown["runner-a"]["help"]
    assert "No agent image" in shown["runner-a"]["help"]
    assert shown["runner-b"]["value"] == (
        "Draining · not answering for 15 minutes, its runs failed"
    )
    # Never the address or the token.
    text = json.dumps(shown)
    assert "https://" not in text
    assert "token" not in text.lower()
