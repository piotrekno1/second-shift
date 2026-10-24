import json
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.test import Client

from secondshift.core.models import Workspace
from secondshift.integrations import model_providers
from secondshift.integrations.models import Connection

KEY = "sk-provider-secret-key"


@pytest.fixture
def owner(db: object) -> tuple[AbstractUser, Workspace]:
    user = get_user_model().objects.create_user(username="owner")
    return user, Workspace.objects.create(name="Test", slug="test", owner=user)


def create(client: Client, **body: Any) -> Any:
    return client.post(
        "/api/connections/model",
        data=json.dumps({"api_key": KEY, **body}),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_first_provider_becomes_default_and_key_is_never_returned(
    client: Client, owner: tuple[AbstractUser, Workspace]
) -> None:
    user, _ = owner
    client.force_login(user)

    first = create(client, provider="anthropic", default_model="claude-opus-5-5")
    second = create(client, provider="openai")
    listed = client.get("/api/connections")

    assert first.status_code == 200
    assert first.json()["is_default"] is True
    assert first.json()["target"] == "api.anthropic.com"
    assert second.json()["is_default"] is False
    for response in (first, second, listed):
        assert KEY not in response.content.decode()

    switched = client.post(
        f"/api/connections/{second.json()['id']}/model",
        data=json.dumps({"is_default": True}),
        content_type="application/json",
    )
    assert switched.json()["is_default"] is True
    assert Connection.objects.filter(is_default=True).count() == 1


@pytest.mark.django_db
def test_validation_rules(
    client: Client, settings: Any, owner: tuple[AbstractUser, Workspace]
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False
    user, _ = owner
    client.force_login(user)

    no_key = create(client, provider="anthropic", api_key="")
    bad_default = create(
        client,
        provider="anthropic",
        allowed_models=["claude-sonnet-5-5"],
        default_model="claude-opus-5-5",
    )
    no_base = create(client, provider="openai_compatible")
    private_base = create(
        client, provider="openai_compatible", base_url="http://127.0.0.1:11434/v1"
    )

    assert no_key.status_code == 422
    assert bad_default.status_code == 422
    assert no_base.status_code == 422
    assert private_base.status_code == 422


@pytest.mark.django_db
def test_local_compatible_provider_in_development(
    client: Client, settings: Any, owner: tuple[AbstractUser, Workspace]
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = True
    user, _ = owner
    client.force_login(user)

    response = create(
        client,
        provider="openai_compatible",
        api_key="",
        base_url="http://localhost:11434/v1/",
    )

    assert response.status_code == 200
    saved = Connection.objects.get(pk=response.json()["id"])
    assert saved.endpoint_url == "http://localhost:11434/v1"
    assert saved.auth_strategy == Connection.AuthStrategy.NONE


@pytest.mark.django_db
def test_check_lists_models_and_flags_unavailable_allowed_ones(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    user, _ = owner
    client.force_login(user)
    created = create(client, provider="anthropic", allowed_models=["claude-gone"])
    monkeypatch.setattr(
        model_providers,
        "list_anthropic_models",
        lambda connection: ["claude-opus-5-5", "claude-sonnet-5-5"],
    )

    response = client.post(f"/api/connections/{created.json()['id']}/test")

    assert response.json()["ok"] is False
    assert "claude-gone" in response.json()["message"]
    assert response.json()["models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    listed = client.get("/api/connections").json()[0]
    assert listed["available_models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]


@pytest.mark.django_db
def test_openai_check_reports_rejected_key(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    user, _ = owner
    client.force_login(user)
    created = create(client, provider="openai")
    seen: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> SimpleNamespace:
        seen.append({"url": url, **kwargs})
        return SimpleNamespace(status_code=401)

    monkeypatch.setattr(
        model_providers,
        "validate_outbound_url",
        lambda url, noun: url,
    )
    monkeypatch.setattr(requests, "get", fake_get)

    response = client.post(f"/api/connections/{created.json()['id']}/test")

    assert seen[0]["url"] == "https://api.openai.com/v1/models"
    assert response.json() == {
        "ok": False,
        "status_code": None,
        "message": "The provider rejected the API key.",
        "models": [],
    }


@pytest.mark.django_db
def test_revoke_discards_key_and_default(
    client: Client, owner: tuple[AbstractUser, Workspace]
) -> None:
    user, _ = owner
    client.force_login(user)
    created = create(client, provider="anthropic")

    revoked = client.post(f"/api/connections/{created.json()['id']}/revoke")
    rename_only = client.post(
        f"/api/connections/{created.json()['id']}/model",
        data=json.dumps({"display_name": "Renamed"}),
        content_type="application/json",
    )

    saved = Connection.objects.get(pk=created.json()["id"])
    assert revoked.json()["is_default"] is False
    assert saved.access_token == ""
    assert rename_only.status_code == 422


class FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body

    def json(self) -> Any:
        return self.body


def compatible_provider(
    client: Client, monkeypatch: pytest.MonkeyPatch, **body: Any
) -> int:
    # Creating validates the URL; skip the DNS lookup in tests.
    monkeypatch.setattr(model_providers, "validate_outbound_url", lambda url, noun: url)
    return create(
        client,
        provider="openai_compatible",
        base_url="https://openrouter.ai/api/v1",
        **{"default_model": "anthropic/claude-haiku-5.5", **body},
    ).json()["id"]


def fake_provider(
    monkeypatch: pytest.MonkeyPatch, call: FakeResponse
) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    monkeypatch.setattr(model_providers, "validate_outbound_url", lambda url, noun: url)
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, **kwargs: FakeResponse(
            200, {"data": [{"id": "anthropic/claude-haiku-5.5"}]}
        ),
    )

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        posts.append({"url": url, **kwargs})
        return call

    monkeypatch.setattr(requests, "post", fake_post)
    return posts


@pytest.mark.django_db
def test_check_catches_a_key_that_lists_models_but_cannot_run_them(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    # OpenRouter lets any key list models; only a real call shows it is unusable.
    client.force_login(owner[0])
    connection_id = compatible_provider(client, monkeypatch)
    posts = fake_provider(
        monkeypatch,
        FakeResponse(401, {"error": {"message": "User not found.", "code": 401}}),
    )

    response = client.post(f"/api/connections/{connection_id}/test").json()

    assert response["ok"] is False
    assert response["message"] == (
        "The provider rejected the API key for model calls: User not found."
    )
    [call] = posts
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["json"]["model"] == "anthropic/claude-haiku-5.5"
    assert call["json"]["max_tokens"] == 16
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["allow_redirects"] is False
    listed = client.get("/api/connections").json()[0]
    assert listed["last_check"]["ok"] is False
    assert "User not found." in listed["last_check"]["message"]
    assert KEY not in json.dumps(listed)


@pytest.mark.django_db
def test_check_passes_when_the_default_model_answers(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    client.force_login(owner[0])
    connection_id = compatible_provider(client, monkeypatch)
    fake_provider(monkeypatch, FakeResponse(200, {"choices": []}))

    response = client.post(f"/api/connections/{connection_id}/test").json()

    assert response["ok"] is True
    assert response["message"] == (
        "Connected. anthropic/claude-haiku-5.5 answered a test request."
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (402, {"error": {"message": "Insufficient credits"}}, "no credit"),
        (
            404,
            {"message": "No such model"},
            "does not offer anthropic/claude-haiku-5.5",
        ),
        (500, "not json", "failed with HTTP 500."),
    ],
)
def test_check_explains_failed_model_calls(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
    status: int,
    body: Any,
    message: str,
) -> None:
    client.force_login(owner[0])
    connection_id = compatible_provider(client, monkeypatch)
    fake_provider(monkeypatch, FakeResponse(status, body))

    response = client.post(f"/api/connections/{connection_id}/test").json()

    assert response["ok"] is False
    assert message in response["message"]


@pytest.mark.django_db
def test_openai_check_calls_the_responses_api(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    client.force_login(owner[0])
    connection_id = create(
        client, provider="openai", default_model="anthropic/claude-haiku-5.5"
    ).json()["id"]
    posts = fake_provider(monkeypatch, FakeResponse(200, {}))

    client.post(f"/api/connections/{connection_id}/test")

    assert posts[0]["url"] == "https://api.openai.com/v1/responses"
    assert posts[0]["json"]["max_output_tokens"] == 16


@pytest.mark.django_db
def test_anthropic_check_sends_one_small_message(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    client.force_login(owner[0])
    connection_id = create(
        client, provider="anthropic", default_model="claude-haiku-5-5"
    ).json()["id"]
    sent: list[dict[str, Any]] = []

    class FakeAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            self.messages = SimpleNamespace(create=lambda **body: sent.append(body))

    monkeypatch.setattr(
        model_providers, "list_anthropic_models", lambda c: ["claude-haiku-5-5"]
    )
    monkeypatch.setattr(model_providers.anthropic, "Anthropic", FakeAnthropic)

    response = client.post(f"/api/connections/{connection_id}/test").json()

    assert response["ok"] is True
    assert sent[0]["model"] == "claude-haiku-5-5"
    assert sent[0]["max_tokens"] == 16


@pytest.mark.django_db
def test_check_without_default_model_makes_no_call_and_edits_clear_old_results(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    client.force_login(owner[0])
    connection_id = compatible_provider(client, monkeypatch, default_model="")
    posts = fake_provider(monkeypatch, FakeResponse(200, {}))

    response = client.post(f"/api/connections/{connection_id}/test").json()
    assert response["ok"] is True
    assert "Set a default model" in response["message"]
    assert posts == []

    client.post(
        f"/api/connections/{connection_id}/model",
        data=json.dumps({"default_model": "anthropic/claude-haiku-5.5"}),
        content_type="application/json",
    )
    assert client.get("/api/connections").json()[0]["last_check"] is None


@pytest.mark.django_db
def test_failed_check_suggests_the_listed_model_id(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    owner: tuple[AbstractUser, Workspace],
) -> None:
    # OpenRouter names models with a vendor prefix and dots.
    client.force_login(owner[0])
    connection_id = compatible_provider(
        client, monkeypatch, default_model="claude-haiku-5-5"
    )
    fake_provider(
        monkeypatch,
        FakeResponse(400, {"error": {"message": "not a valid model ID"}}),
    )

    response = client.post(f"/api/connections/{connection_id}/test").json()

    assert response["ok"] is False
    assert response["message"].endswith(
        "The provider lists it as anthropic/claude-haiku-5.5."
    )


def test_closest_model_ignores_unrelated_names() -> None:
    models = ["openai/gpt-5", "anthropic/claude-haiku-5.5"]

    assert model_providers.closest_model("Claude_Haiku_5.5", models) == (
        "anthropic/claude-haiku-5.5"
    )
    assert model_providers.closest_model("llama-3-70b", models) is None
