import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpcore
import httpx
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db.models import F
from django.test import Client
from django.urls import Resolver404, resolve
from django.utils import timezone

from secondshift.automations.models import Job, Run
from secondshift.automations.reconciliation import mark_run_terminal
from secondshift.gateway.metering import StreamMeter, Usage, record_usage
from secondshift.gateway.models import ModelUsage, RunToken
from secondshift.gateway.pricing import request_cost
from secondshift.gateway.proxy import upstream_client
from secondshift.gateway.tokens import GatewayUnavailable, authenticate, issue_run_token
from secondshift.gateway.views import model_gateway
from secondshift.integrations.models import Connection
from secondshift.integrations.outbound import PinnedNetworkBackend, PinnedTransport
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace
from secondshift.workspaces.services import create_workspace, workspace_owner

PROVIDER_KEY = "sk-ant-provider-secret"
pytestmark = pytest.mark.urls("secondshift.gateway.urls")


def fake_upstream(
    status_code: int = 200,
    body: bytes = b"{}",
    content_type: str = "application/json",
    chunks: list[bytes] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    async def stream() -> AsyncIterator[bytes]:
        for chunk in chunks or []:
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"content-type": content_type, "request-id": "req_123"},
            content=stream() if chunks is not None else body,
        )

    return handler


@pytest.fixture
def workspace(db: object) -> Workspace:
    user = get_user_model().objects.create_user(username="owner")
    return create_workspace(name="Test", slug="test", owner=user)


def make_run(workspace: Workspace, status: str = Run.Status.RUNNING) -> Run:
    repository = Repository.objects.create(
        workspace=workspace, full_name=f"local/{workspace.slug}", config_sha="abc"
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
    return Run.objects.create(
        job=job,
        status=status,
        commit_sha="abc",
        scheduled_for=datetime(2026, 10, 1, tzinfo=UTC),
    )


def provider(
    workspace: Workspace,
    kind: str = Connection.Provider.ANTHROPIC,
    allowed: list[str] | None = None,
) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=kind,
        kind=Connection.Kind.MODEL,
        display_name="Provider",
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token=PROVIDER_KEY,
        allowed_models=allowed or [],
        is_default=True,
    )


def capture_post(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        "secondshift.gateway.proxy.upstream_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(record)),
    )
    return calls


def drain(response: Any) -> bytes:
    async def collect() -> bytes:
        return b"".join([chunk async for chunk in response.streaming_content])

    return async_to_sync(collect)()


def post(client: Client, path: str, body: dict[str, Any], **headers: str) -> Any:
    return client.post(
        path,
        data=json.dumps(body),
        content_type="application/json",
        headers=headers,
    )


@pytest.mark.django_db
def test_tokens_are_hashed_and_die_with_the_run(workspace: Workspace) -> None:
    run = make_run(workspace)
    token = issue_run_token(run, provider(workspace))

    stored = RunToken.objects.get(run=run)
    assert token not in stored.token_hash
    assert authenticate(token) is not None

    mark_run_terminal(run.pk, Run.Status.FAILED, "stopped", workflow_status="ERROR")
    assert authenticate(token) is None


@pytest.mark.django_db
def test_expired_and_foreign_tokens_are_rejected(workspace: Workspace) -> None:
    run = make_run(workspace)
    token = issue_run_token(run, provider(workspace))
    RunToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert authenticate(token) is None

    other_user = get_user_model().objects.create_user(username="other")
    other = create_workspace(name="Other", slug="other", owner=other_user)
    with pytest.raises(GatewayUnavailable):
        issue_run_token(run, provider(other))


@pytest.mark.django_db
def test_gateway_injects_provider_key_and_meters_usage(
    client: Client, monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    run = make_run(workspace)
    token = issue_run_token(run, provider(workspace))
    body = {
        "id": "msg_1",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 5,
        },
    }
    calls = capture_post(monkeypatch, fake_upstream(body=json.dumps(body).encode()))

    response = post(
        client,
        "/gateway/anthropic/v1/messages",
        {"model": "claude-opus-5-5", "max_tokens": 16000, "messages": []},
        **{"x-api-key": token, "anthropic-beta": "some-beta"},
    )

    assert response.status_code == 200
    assert response["request-id"] == "req_123"
    sent = calls[0]
    assert str(sent.url) == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == PROVIDER_KEY
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert sent.headers["anthropic-beta"] == "some-beta"
    assert token not in json.dumps(dict(sent.headers))
    usage = ModelUsage.objects.get(run=run)
    assert (usage.input_tokens, usage.output_tokens) == (100, 20)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (300, 5)
    assert RunToken.objects.get(run=run).used_tokens == 425
    assert usage.cost_usd is not None and usage.cost_usd > 0


@pytest.mark.django_db
def test_gateway_enforces_auth_paths_models_and_budget(
    client: Client, monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    run = make_run(workspace)
    token = issue_run_token(run, provider(workspace, allowed=["claude-sonnet-5-5"]))
    calls = capture_post(monkeypatch, fake_upstream())
    message = {"model": "claude-sonnet-5-5", "max_tokens": 10, "messages": []}

    unauthenticated = post(client, "/gateway/anthropic/v1/messages", message)
    wrong_family = post(
        client,
        "/gateway/openai/v1/chat/completions",
        message,
        authorization=f"Bearer {token}",
    )
    other_path = post(
        client, "/gateway/anthropic/v1/files", message, **{"x-api-key": token}
    )
    other_model = post(
        client,
        "/gateway/anthropic/v1/messages",
        {**message, "model": "claude-opus-5-5"},
        **{"x-api-key": token},
    )
    RunToken.objects.update(used_tokens=F("budget_tokens"))
    over_budget = post(
        client, "/gateway/anthropic/v1/messages", message, **{"x-api-key": token}
    )

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["type"] == "authentication_error"
    assert wrong_family.status_code == 404
    assert other_path.status_code == 404
    assert other_model.status_code == 403
    assert over_budget.status_code == 429
    # The provider SDK must not retry a run that has used its budget.
    assert over_budget["x-should-retry"] == "false"
    assert calls == []


@pytest.mark.django_db
def test_anthropic_stream_passes_through_and_is_metered(
    client: Client, monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    run = make_run(workspace)
    token = issue_run_token(run, provider(workspace))
    stream = (
        b'event: message_start\ndata: {"type":"message_start","message":'
        b'{"usage":{"input_tokens":40,"output_tokens":1,"cache_read_input_tokens":7}}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta",'
        b'"delta":{"type":"text_delta","text":"hi"}}\n\n'
        b'event: message_delta\ndata: {"type":"message_delta",'
        b'"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":12}}\n\n'
    )
    # Split mid-line to prove the meter buffers partial events.
    chunks = [stream[:50], stream[50:131], stream[131:]]
    capture_post(
        monkeypatch,
        fake_upstream(content_type="text/event-stream", chunks=chunks),
    )

    response = post(
        client,
        "/gateway/anthropic/v1/messages",
        {
            "model": "claude-opus-5-5",
            "max_tokens": 64000,
            "stream": True,
            "messages": [],
        },
        **{"x-api-key": token},
    )
    received = drain(response)

    assert received == stream
    usage = ModelUsage.objects.get(run=run)
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (
        40,
        12,
        7,
    )


@pytest.mark.django_db
def test_openai_stream_requests_usage_and_splits_cached_tokens(
    client: Client, monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    run = make_run(workspace)
    connection = provider(workspace, kind=Connection.Provider.OPENAI)
    token = issue_run_token(run, connection)
    stream = (
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":50,"completion_tokens":9,'
        b'"prompt_tokens_details":{"cached_tokens":30}}}\n\n'
        b"data: [DONE]\n\n"
    )
    calls = capture_post(
        monkeypatch, fake_upstream(content_type="text/event-stream", chunks=[stream])
    )

    response = post(
        client,
        "/gateway/openai/v1/chat/completions",
        {"model": "gpt-test", "stream": True, "messages": []},
        authorization=f"Bearer {token}",
    )
    drain(response)

    sent = calls[0]
    assert str(sent.url) == "https://api.openai.com/v1/chat/completions"
    assert sent.headers["Authorization"] == f"Bearer {PROVIDER_KEY}"
    assert json.loads(sent.content)["stream_options"] == {"include_usage": True}
    usage = ModelUsage.objects.get(run=run)
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (
        20,
        30,
        9,
    )


def test_stream_meter_ignores_malformed_events() -> None:
    meter = StreamMeter("anthropic")
    meter.feed(b'data: not json\n\ndata: [1, 2]\n\ndata: {"type": "message_delta"}\n')
    assert meter.usage.total == 0


def test_gateway_is_served_only_by_the_gateway_process() -> None:
    assert resolve(
        "/gateway/anthropic/v1/messages", "secondshift.gateway.urls"
    ).func is (model_gateway)
    assert resolve(
        "/gateway/anthropic/v1/messages", "secondshift.config.urls"
    ).func is not (model_gateway)
    with pytest.raises(Resolver404):
        resolve("/api/runs", "secondshift.gateway.urls")


@pytest.mark.urls("secondshift.config.urls")
@pytest.mark.django_db
def test_cost_is_priced_per_provider_and_summed_per_run(
    client: Client, workspace: Workspace
) -> None:
    run = make_run(workspace)
    anthropic = provider(workspace)
    compatible = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.OPENAI_COMPATIBLE,
        kind=Connection.Kind.MODEL,
        display_name="Local",
        endpoint_url="http://localhost:11434/v1",
    )
    usage = Usage(input_tokens=1_000, output_tokens=100)

    assert request_cost(anthropic, "claude-opus-5-5", Usage()) == Decimal(0)
    priced = request_cost(anthropic, "claude-opus-5-5", usage)
    assert priced is not None and priced > 0
    assert request_cost(compatible, "llama-local", usage) is None

    for connection, model in ((anthropic, "claude-opus-5-5"), (compatible, "llama")):
        token = RunToken.objects.create(
            run=run,
            connection=connection,
            token_hash=model,
            expires_at=timezone.now(),
            budget_tokens=10_000,
        )
        async_to_sync(record_usage)(token, "anthropic", model, 200, usage)

    client.force_login(workspace_owner(workspace))
    listed = client.get("/api/runs").json()["items"][0]
    assert listed["model_tokens"] == 2_200
    assert listed["model_cost_usd"] == pytest.approx(float(priced))
    assert listed["model_cost_partial"] is True
    assert listed["model_requests"] == 2
    assert (listed["model_input_tokens"], listed["model_output_tokens"]) == (2_000, 200)


def test_upstream_client_pins_addresses_and_never_follows_redirects() -> None:
    async def build() -> httpx.AsyncClient:
        return upstream_client()

    client = async_to_sync(build)()
    assert client.follow_redirects is False
    assert isinstance(client._transport, PinnedTransport)


@pytest.mark.parametrize(
    "addresses", [["10.0.0.5"], ["93.184.215.14", "127.0.0.1"], ["::1"]]
)
def test_pinned_backend_refuses_private_answers(
    settings: Any, monkeypatch: pytest.MonkeyPatch, addresses: list[str]
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False

    async def fake_getaddrinfo(host: str, port: int, **_: Any) -> list[Any]:
        return [(None, None, None, "", (address, port)) for address in addresses]

    connected: list[str] = []

    async def fake_connect(self: Any, host: str, port: int, **_: Any) -> None:
        connected.append(host)

    monkeypatch.setattr(
        "secondshift.integrations.outbound.anyio.getaddrinfo", fake_getaddrinfo
    )
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)

    with pytest.raises(httpcore.ConnectError):
        async_to_sync(PinnedNetworkBackend().connect_tcp)("rebound.example", 443)
    assert connected == []


def test_pinned_backend_connects_to_the_vetted_address(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.OUTBOUND_ALLOW_LOCAL = False
    answers = iter([["93.184.215.14"], ["127.0.0.1"]])

    async def fake_getaddrinfo(host: str, port: int, **_: Any) -> list[Any]:
        return [(None, None, None, "", (address, port)) for address in next(answers)]

    connected: list[str] = []

    async def fake_connect(self: Any, host: str, port: int, **_: Any) -> str:
        connected.append(host)
        return "stream"

    monkeypatch.setattr(
        "secondshift.integrations.outbound.anyio.getaddrinfo", fake_getaddrinfo
    )
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", fake_connect)
    backend = PinnedNetworkBackend()

    assert async_to_sync(backend.connect_tcp)("api.example", 443) == "stream"
    # A later answer that rebinds to a private address is refused.
    with pytest.raises(httpcore.ConnectError):
        async_to_sync(backend.connect_tcp)("api.example", 443)
    assert connected == ["93.184.215.14"]
