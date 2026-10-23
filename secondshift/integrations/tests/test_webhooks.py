import json
import socket
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db import connection as db_connection
from django.test import Client

from secondshift.automations.models import CapabilityRequest, Job, JobGrant
from secondshift.core.models import Workspace
from secondshift.integrations.models import Connection
from secondshift.integrations.webhooks import (
    WebhookError,
    deliver_webhook,
    validate_webhook_url,
)
from secondshift.repositories.models import Repository

SECRET_URL = "https://hooks.example.com/services/T000/B000/secret-token"


def resolve_to(address: str) -> Any:
    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    return fake_getaddrinfo


@pytest.fixture
def owner(db: object) -> tuple[AbstractUser, Workspace]:
    user = get_user_model().objects.create_user(username="owner")
    workspace = Workspace.objects.create(name="Test", slug="test", owner=user)
    return user, workspace


def webhook(workspace: Workspace, url: str = SECRET_URL) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.WEBHOOK,
        kind=Connection.Kind.NOTIFICATION,
        binding_key="engineering-alerts",
        display_name="Alerts",
        auth_strategy=Connection.AuthStrategy.NONE,
        delivery_url=url,
        metadata={"target": "hooks.example.com"},
    )


@pytest.mark.parametrize(
    ("url", "address"),
    [
        ("http://hooks.example.com/x", "93.184.216.34"),
        ("https://user:pass@hooks.example.com/x", "93.184.216.34"),
        ("https://internal.example.com/x", "10.0.0.5"),
        ("https://metadata.example.com/x", "169.254.169.254"),
        ("https://localhost/x", "127.0.0.1"),
        ("file:///etc/passwd", "93.184.216.34"),
    ],
)
def test_production_rejects_unsafe_webhook_urls(
    settings: Any, monkeypatch: pytest.MonkeyPatch, url: str, address: str
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False
    monkeypatch.setattr(socket, "getaddrinfo", resolve_to(address))

    with pytest.raises(WebhookError) as error:
        validate_webhook_url(url)

    assert "secret" not in str(error.value)
    assert url not in str(error.value)


def test_production_accepts_public_https(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False
    monkeypatch.setattr(socket, "getaddrinfo", resolve_to("93.184.216.34"))

    assert validate_webhook_url(f" {SECRET_URL} ") == SECRET_URL


def test_development_allows_local_http(settings: Any) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True

    url = "http://localhost:9000/hook"
    assert validate_webhook_url(url) == url


@pytest.mark.django_db
def test_delivery_does_not_follow_redirects_or_leak_url(
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        raise requests.ConnectionError(f"cannot connect to {url}")

    monkeypatch.setattr(requests, "post", fake_post)

    delivery = deliver_webhook(webhook(owner[1]), {"text": "hi"})

    assert calls[0]["allow_redirects"] is False
    assert delivery.ok is False
    assert delivery.retryable is True
    assert "secret-token" not in delivery.message


@pytest.mark.django_db
def test_owner_creates_webhook_without_url_in_response(
    client: Client,
    settings: Any,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    user, _ = owner
    client.force_login(user)

    response = client.post(
        "/api/connections/webhook",
        data=json.dumps(
            {
                "binding_key": "engineering-alerts",
                "display_name": "Engineering alerts",
                "url": SECRET_URL,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "notification"
    assert payload["provider"] == "webhook"
    assert payload["target"] == "hooks.example.com"
    assert "secret-token" not in response.content.decode()
    saved = Connection.objects.get(pk=payload["id"])
    assert saved.delivery_url == SECRET_URL
    with db_connection.cursor() as cursor:
        cursor.execute(
            "SELECT delivery_url FROM integrations_connection WHERE id = %s",
            [saved.pk],
        )
        assert "secret-token" not in cursor.fetchone()[0]


@pytest.mark.django_db
def test_duplicate_binding_is_rejected(
    client: Client,
    settings: Any,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    user, workspace = owner
    webhook(workspace)
    client.force_login(user)

    response = client.post(
        "/api/connections/webhook",
        data=json.dumps({"binding_key": "engineering-alerts", "url": SECRET_URL}),
        content_type="application/json",
    )

    assert response.status_code == 409


@pytest.mark.django_db
def test_connection_test_sends_message(
    client: Client,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    user, workspace = owner
    connection = webhook(workspace)
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> SimpleNamespace:
        sent.append(kwargs["json"])
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(requests, "post", fake_post)
    client.force_login(user)

    response = client.post(f"/api/connections/{connection.pk}/test")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status_code": 204,
        "message": "Delivered (HTTP 204).",
        "models": [],
    }
    assert sent[0]["event"] == "connection.test"


@pytest.mark.django_db
def test_members_cannot_create_or_test_webhooks(
    client: Client,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    _, workspace = owner
    connection = webhook(workspace)
    member = get_user_model().objects.create_user(username="member")
    workspace.memberships.create(user=member)
    client.force_login(member)

    create = client.post(
        "/api/connections/webhook",
        data=json.dumps({"binding_key": "other", "url": SECRET_URL}),
        content_type="application/json",
    )
    test = client.post(f"/api/connections/{connection.pk}/test")

    assert create.status_code == 403
    assert test.status_code == 403


@pytest.mark.django_db
def test_owner_rotates_url_and_reconnects_after_revoke(
    client: Client,
    settings: Any,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    user, workspace = owner
    connection = webhook(workspace)
    client.force_login(user)

    revoked = client.post(f"/api/connections/{connection.pk}/revoke")
    connection.refresh_from_db()
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["target"] == ""
    assert connection.delivery_url == ""

    rename_only = client.post(
        f"/api/connections/{connection.pk}/webhook",
        data=json.dumps({"display_name": "Renamed"}),
        content_type="application/json",
    )
    assert rename_only.status_code == 409

    reconnected = client.post(
        f"/api/connections/{connection.pk}/webhook",
        data=json.dumps(
            {"display_name": "Renamed", "url": "https://chat.example.org/hook/new"}
        ),
        content_type="application/json",
    )
    assert reconnected.status_code == 200
    assert reconnected.json()["status"] == "connected"
    assert reconnected.json()["target"] == "chat.example.org"
    assert "hook/new" not in reconnected.content.decode()


@pytest.mark.django_db
def test_deleting_connection_returns_grants_to_pending(
    client: Client,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    user, workspace = owner
    connection = webhook(workspace)
    repository = Repository.objects.create(
        workspace=workspace, full_name="local/project", config_sha="abc"
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="health",
        name="Health",
        skill_path=".agents/skills/health/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )
    capability = CapabilityRequest.objects.create(
        job=job, binding="engineering-alerts", kind="notification"
    )
    grant = JobGrant.objects.create(
        capability_request=capability,
        connection=connection,
        status=JobGrant.Status.APPROVED,
        reviewed_by=user,
    )
    client.force_login(user)

    response = client.delete(f"/api/connections/{connection.pk}")

    assert response.status_code == 204
    assert not Connection.objects.filter(pk=connection.pk).exists()
    grant.refresh_from_db()
    assert grant.status == JobGrant.Status.PENDING
    assert grant.connection is None


@pytest.mark.django_db
def test_identity_connections_cannot_be_removed(
    client: Client,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    user, workspace = owner
    identity = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.IDENTITY,
        display_name="octocat",
    )
    client.force_login(user)

    assert client.post(f"/api/connections/{identity.pk}/revoke").status_code == 409
    assert client.delete(f"/api/connections/{identity.pk}").status_code == 409
