import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.http import HttpResponse
from django.test import Client, override_settings

from secondshift.workspaces.models import Workspace
from secondshift.workspaces.services import OWNED_WORKSPACE_LIMIT, create_workspace


def user(name: str, *, staff: bool = False) -> AbstractUser:
    return get_user_model().objects.create_user(
        username=name, email=f"{name}@x.dev", is_staff=staff
    )


def create(client: Client, name: str) -> HttpResponse:
    return client.post(
        "/api/workspaces",
        data=json.dumps({"name": name}),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_staff_create_a_workspace_with_sign_up_closed(client: Client) -> None:
    operator = user("operator", staff=True)
    create_workspace("Acme", "acme", operator)
    client.force_login(operator)
    assert client.get("/api/me").json()["can_create_workspace"] is True

    response = create(client, "Acme")

    assert response.status_code == 200
    body = response.json()
    # The name is taken, so the slug gets a number. The new one becomes current.
    assert (body["name"], body["slug"], body["role"], body["current"]) == (
        "Acme",
        "acme-2",
        "owner",
        True,
    )
    me = client.get("/api/me").json()
    assert (me["workspace_slug"], me["role"]) == ("acme-2", "owner")


@pytest.mark.django_db
def test_people_who_are_not_staff_cannot_with_sign_up_closed(client: Client) -> None:
    person = user("person")
    client.force_login(person)
    assert client.get("/api/me").json()["can_create_workspace"] is False

    response = create(client, "Mine")

    assert response.status_code == 403
    assert not Workspace.objects.exists()


@pytest.mark.django_db
@override_settings(SIGNUP_ENABLED=True)
def test_anyone_can_with_sign_up_open_up_to_a_limit(client: Client) -> None:
    client.force_login(user("person"))
    assert client.get("/api/me").json()["can_create_workspace"] is True

    for number in range(OWNED_WORKSPACE_LIMIT):
        assert create(client, f"Team {number}").status_code == 200
    refused = create(client, "One too many")

    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        f"You can own up to {OWNED_WORKSPACE_LIMIT} workspaces."
    )


@pytest.mark.django_db
def test_a_workspace_needs_a_name(client: Client) -> None:
    client.force_login(user("operator", staff=True))

    response = create(client, "   ")

    assert response.status_code == 400
    assert response.json()["detail"] == "Give the workspace a name."
