import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.http import HttpResponse
from django.test import Client

from secondshift.automations.models import Job
from secondshift.repositories.models import Repository
from secondshift.workspaces.access import (
    ADMIN_ACTIONS,
    POLICY,
    Action,
    Role,
    role_allows,
)
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace


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
def test_me_reports_the_role_and_what_it_allows(
    client: Client, team: dict[str, object]
) -> None:
    client.force_login(team[Role.ADMIN])

    body = client.get("/api/me").json()

    assert body["workspace_slug"] == "team"
    assert body["role"] == "admin"
    assert set(body["permissions"]) == set(ADMIN_ACTIONS)


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
    assert body["role"] is None
    assert body["permissions"] == []
    assert client.get("/api/workspaces").json() == []
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


@pytest.mark.django_db
def test_a_person_works_in_one_workspace_at_a_time(client: Client) -> None:
    person = user("person")
    first = create_workspace("First", "first", person)
    second = create_workspace("Second", "second", user("other"))
    second.memberships.create(user=person, role=Role.MEMBER)
    job_in(first)
    job_in(second)
    client.force_login(person)

    # The first workspace joined is the default.
    assert client.get("/api/me").json()["workspace_slug"] == "first"
    assert [repo["full_name"] for repo in client.get("/api/repositories").json()] == [
        "local/first"
    ]
    choices = client.get("/api/workspaces").json()
    assert [(item["slug"], item["role"], item["current"]) for item in choices] == [
        ("first", "owner", True),
        ("second", "member", False),
    ]

    chosen = post(client, "/api/workspaces/current", {"workspace_id": second.pk})

    assert chosen.status_code == 200
    assert (chosen.json()["slug"], chosen.json()["current"]) == ("second", True)
    me = client.get("/api/me").json()
    assert (me["workspace_slug"], me["role"], me["permissions"]) == (
        "second",
        "member",
        [],
    )
    assert [repo["full_name"] for repo in client.get("/api/repositories").json()] == [
        "local/second"
    ]
    # The role follows the workspace: the person owns First but not Second.
    assert post(client, "/api/settings/limits", {"max_turns": 5}).status_code == 403


@pytest.mark.django_db
def test_only_members_can_choose_a_workspace(client: Client) -> None:
    person = user("person")
    create_workspace("Mine", "mine", person)
    theirs = create_workspace("Theirs", "theirs", user("stranger"))
    client.force_login(person)

    response = post(client, "/api/workspaces/current", {"workspace_id": theirs.pk})

    assert response.status_code == 404
    assert client.get("/api/me").json()["workspace_slug"] == "mine"


@pytest.mark.django_db
def test_leaving_the_chosen_workspace_falls_back_to_the_first(client: Client) -> None:
    person = user("person")
    create_workspace("Mine", "mine", person)
    other = create_workspace("Other", "other", user("other"))
    other.memberships.create(user=person, role=Role.ADMIN)
    client.force_login(person)
    post(client, "/api/workspaces/current", {"workspace_id": other.pk})

    WorkspaceMembership.objects.filter(workspace=other, user=person).delete()

    assert client.get("/api/me").json()["workspace_slug"] == "mine"
