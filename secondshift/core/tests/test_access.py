import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.http import HttpResponse
from django.test import Client

from secondshift.automations.models import Job
from secondshift.core.access import ADMIN_ACTIONS, POLICY, Action, Role, role_allows
from secondshift.core.models import Workspace
from secondshift.core.services import create_workspace
from secondshift.repositories.models import Repository


def user(name: str) -> AbstractUser:
    return get_user_model().objects.create_user(username=name, email=f"{name}@x.dev")


def post(client: Client, path: str, body: dict[str, object]) -> HttpResponse:
    return client.post(path, data=json.dumps(body), content_type="application/json")


def job_in(workspace: Workspace) -> Job:
    repository = Repository.objects.create(
        workspace=workspace,
        full_name=f"local/{workspace.slug}",
        source_type=Repository.SourceType.LOCAL,
        local_path="/tmp/project",
        enabled=True,
    )
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        timezone="UTC",
    )


@pytest.fixture
def team(db: object) -> dict[str, object]:
    owner = user("owner")
    workspace = create_workspace("Team", "team", owner)
    people: dict[str, object] = {"workspace": workspace, "owner": owner}
    for role in (Role.ADMIN, Role.MEMBER):
        person = user(role)
        workspace.memberships.create(user=person, role=role)
        people[role] = person
    return people


def test_every_action_needs_a_role_and_members_can_only_view() -> None:
    assert set(POLICY) == set(Action)
    for action in Action:
        assert role_allows(Role.OWNER, action)
        assert role_allows(Role.ADMIN, action) is (action in ADMIN_ACTIONS)
        assert not role_allows(Role.MEMBER, action)
        assert not role_allows(None, action)


@pytest.mark.django_db
def test_admins_run_the_work_but_cannot_touch_credentials(
    client: Client, team: dict[str, object]
) -> None:
    client.force_login(team[Role.ADMIN])

    limits = post(client, "/api/settings/limits", {"max_turns": 10})
    webhook = post(
        client,
        "/api/connections/webhook",
        {"binding_key": "alerts", "url": "https://hooks.example.com/x"},
    )

    assert limits.status_code == 200
    assert limits.json()["can_edit_limits"] is True
    assert webhook.status_code == 403
    assert webhook.json()["detail"] == "Only workspace owners can add connections."


@pytest.mark.django_db
def test_objects_in_another_workspace_are_not_found(client: Client) -> None:
    person = user("person")
    create_workspace("Mine", "mine", person)
    theirs = job_in(create_workspace("Theirs", "theirs", user("stranger")))
    client.force_login(person)

    assert client.get(f"/api/jobs/{theirs.pk}").status_code == 404
    assert client.post(f"/api/jobs/{theirs.pk}/run").status_code == 404
    assert client.get(f"/api/repositories/{theirs.repository_id}").status_code == 404


@pytest.mark.django_db
def test_a_person_without_a_workspace_can_do_nothing(client: Client) -> None:
    client.force_login(user("newcomer"))

    body = client.get("/api/me").json()

    assert body["workspace_slug"] is None
    assert post(client, "/api/settings/limits", {"max_turns": 5}).status_code == 403


@pytest.mark.django_db
def test_members_cannot_start_runs(client: Client, team: dict[str, object]) -> None:
    job = job_in(team["workspace"])  # type: ignore[arg-type]
    client.force_login(team[Role.MEMBER])

    response = client.post(f"/api/jobs/{job.pk}/run")

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Only workspace owners and admins can run jobs."
    )
