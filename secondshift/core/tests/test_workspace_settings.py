import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from secondshift.core.models import Workspace, WorkspaceMembership
from secondshift.core.workspace_settings import image_label


@pytest.mark.django_db
def test_settings_show_members_and_effective_limits_without_secrets(
    client: Client, settings: object
) -> None:
    settings.RUN_TIMEOUT_SECONDS = 900  # type: ignore[attr-defined]
    settings.RUN_RETENTION_DAYS = 0  # type: ignore[attr-defined]
    settings.AGENT_MAX_TURNS = 24  # type: ignore[attr-defined]
    settings.RUNNER_RUNTIME = "runsc"  # type: ignore[attr-defined]
    users = get_user_model().objects
    owner = users.create_user(username="owner", email="owner@example.com")
    teammate = users.create_user(username="teammate", email="team@example.com")
    workspace = Workspace.objects.create(name="Team", slug="team", owner=owner)
    WorkspaceMembership.objects.create(workspace=workspace, user=owner, role="owner")
    WorkspaceMembership.objects.create(workspace=workspace, user=teammate, role="admin")
    client.force_login(owner)

    body = client.get("/api/settings").json()

    assert body["workspace_name"] == "Team"
    assert body["members"] == [
        {"email": "owner@example.com", "role": "Owner"},
        {"email": "team@example.com", "role": "Admin"},
    ]
    values = {
        item["label"]: (item["value"], item["env"])
        for group in body["groups"]
        for item in group["items"]
    }
    assert values["Run time limit"] == ("15 minutes", "SECONDSHIFT_RUN_TIMEOUT_SECONDS")
    assert values["History kept for"][0] == "Forever"
    assert values["Model calls per run"] == ("24", "SECONDSHIFT_AGENT_MAX_TURNS")
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
