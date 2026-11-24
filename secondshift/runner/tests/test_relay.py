"""The runner keeps its agents' tool calls until the worker answers them."""

import base64
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from django.test import Client

from secondshift.runner import relay, views
from secondshift.runner.models import AgentCall, AgentRelay, RunnerSession
from secondshift.runner.relay import (
    answer_call,
    collect_calls,
    relaying,
    submit_call,
    wait_for_answer,
)
from secondshift.runner_protocol.agents import AGENT_BROKER_PATH
from secondshift.runner_protocol.relay import RELAY_OFF, RelayedAnswer

pytestmark = [pytest.mark.django_db, pytest.mark.urls("secondshift.runner.urls")]

RELAY_PORT = 8003
BROKER_TOKEN = "broker-token"
CALL = b'{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'


@pytest.fixture
def relay_on(settings: Any, tmp_path: Path) -> str:
    settings.AGENT_RELAY_PORT = RELAY_PORT
    RunnerSession.objects.create(
        session_id="run-1",
        run_id=1,
        repository="example/agents",
        commit_sha="a" * 40,
        image="test",
        container_name="sandbox",
        workspace_path=str(tmp_path / "workspace"),
        status="active",
    )
    return settings.RUNNER_SERVICE_TOKEN


def agent_post(body: bytes, token: str = BROKER_TOKEN, port: int = RELAY_PORT) -> Any:
    return Client().post(
        f"/{AGENT_BROKER_PATH}",
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        SERVER_PORT=str(port),
    )


def api(service_token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {service_token}"}


def test_an_agent_call_is_answered_with_the_worker_s_answer(
    relay_on: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = b'{"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}'
    real_wait = relay.wait_for_answer

    def worker_answers(call_id: UUID) -> RelayedAnswer | None:
        # What the worker does while the agent's request waits.
        collected = Client().get("/v1/sessions/run-1/agent/calls", **api(relay_on))
        [call] = collected.json()["calls"]
        assert base64.b64decode(call["body"]) == CALL
        stored = Client().post(
            f"/v1/sessions/run-1/agent/calls/{call['id']}?status=200",
            data=answer,
            content_type="application/octet-stream",
            **api(relay_on),
        )
        assert stored.status_code == 204
        return real_wait(call_id, timeout=1)

    monkeypatch.setattr(views, "wait_for_answer", worker_answers)

    with relaying("run-1", BROKER_TOKEN):
        response = agent_post(CALL)

    assert response.status_code == 200
    assert response.content == answer
    assert not AgentCall.objects.exists()


def test_the_relay_is_served_only_on_its_port_and_only_when_on(
    relay_on: str, settings: Any
) -> None:
    with relaying("run-1", BROKER_TOKEN):
        assert agent_post(CALL, port=8002).status_code == 404
        settings.AGENT_RELAY_PORT = 0
        assert agent_post(CALL).status_code == 404


def test_the_runner_api_is_never_served_on_the_relay_port(relay_on: str) -> None:
    answer = Client().get(
        "/v1/environments", SERVER_PORT=str(RELAY_PORT), **api(relay_on)
    )

    assert answer.status_code == 401


def test_the_relay_refuses_a_wrong_token_and_an_oversized_call(
    relay_on: str,
) -> None:
    with relaying("run-1", BROKER_TOKEN):
        assert agent_post(CALL, token="other").status_code == 401
        assert agent_post(CALL, token="").status_code == 401
        assert agent_post(b"x" * (1024 * 1024 + 1)).status_code == 413
        assert (
            Client().get(f"/{AGENT_BROKER_PATH}", SERVER_PORT=str(RELAY_PORT))
        ).status_code == 405
    # Once the agent ends, its token is refused too.
    assert agent_post(CALL).status_code == 401
    assert not AgentCall.objects.exists()


def test_an_agent_has_only_a_few_calls_waiting(relay_on: str) -> None:
    with relaying("run-1", BROKER_TOKEN):
        for _ in range(relay.MAX_UNANSWERED_CALLS):
            submit_call(BROKER_TOKEN, CALL)

        assert agent_post(CALL).status_code == 429


def test_each_call_is_collected_once(relay_on: str) -> None:
    with relaying("run-1", BROKER_TOKEN):
        submit_call(BROKER_TOKEN, CALL)

        first = collect_calls("run-1", wait=0)
        again = collect_calls("run-1", wait=0)

    assert [call.body for call in first] == [CALL]
    assert again == []


def test_collecting_waits_for_a_call(relay_on: str) -> None:
    with relaying("run-1", BROKER_TOKEN):

        def agent_calls(seconds: float) -> None:
            submit_call(BROKER_TOKEN, CALL)

        calls = collect_calls("run-1", wait=5, sleep=agent_calls)

    assert [call.body for call in calls] == [CALL]


def test_an_unanswered_call_times_out_and_is_removed(relay_on: str) -> None:
    clock = [0.0]

    def later(seconds: float) -> None:
        clock[0] += 400

    with relaying("run-1", BROKER_TOKEN):
        call_id = submit_call(BROKER_TOKEN, CALL)

        assert call_id is not None
        assert wait_for_answer(call_id, sleep=later, clock=lambda: clock[0]) is None
        assert not AgentCall.objects.exists()


def test_an_answer_counts_only_for_a_collected_call_of_that_session(
    relay_on: str,
) -> None:
    with relaying("run-1", BROKER_TOKEN):
        call_id = submit_call(BROKER_TOKEN, CALL)
        assert call_id is not None
        reply = RelayedAnswer(200, b"{}")

        # Not collected yet.
        assert answer_call("run-1", call_id, reply) is False
        collect_calls("run-1", wait=0)
        assert answer_call("run-2", call_id, reply) is False
        assert answer_call("run-1", call_id, reply) is True
        # Answered once.
        assert answer_call("run-1", call_id, reply) is False


def test_an_answer_larger_than_the_runner_s_upload_limit_is_kept(
    relay_on: str,
) -> None:
    large = json.dumps({"result": "x" * 200_000}).encode()
    with relaying("run-1", BROKER_TOKEN):
        call_id = submit_call(BROKER_TOKEN, CALL)
        collect_calls("run-1", wait=0)

        stored = Client().post(
            f"/v1/sessions/run-1/agent/calls/{call_id}?status=200",
            data=large,
            content_type="application/octet-stream",
            **api(relay_on),
        )

        assert stored.status_code == 204
        assert call_id is not None
        answer = wait_for_answer(call_id, timeout=0)
        assert answer == RelayedAnswer(200, large)


def test_an_answer_for_a_call_that_is_gone_is_dropped(relay_on: str) -> None:
    stored = Client().post(
        "/v1/sessions/run-1/agent/calls/6f1c1c2e-8f43-4d5c-9a8e-3c0b7f0d2a11"
        "?status=200",
        data=b"{}",
        content_type="application/octet-stream",
        **api(relay_on),
    )

    assert stored.status_code == 204


def test_a_runner_that_does_not_relay_says_so(settings: Any) -> None:
    token = settings.RUNNER_SERVICE_TOKEN

    answer = Client().get("/v1/sessions/run-1/agent/calls", **api(token))

    assert answer.status_code == 404
    assert answer.json() == {"error": RELAY_OFF}


def test_the_relay_and_its_calls_end_with_the_agent(relay_on: str) -> None:
    with relaying("run-1", BROKER_TOKEN):
        submit_call(BROKER_TOKEN, CALL)
        assert AgentRelay.objects.exists()

    assert not AgentRelay.objects.exists()
    assert not AgentCall.objects.exists()


def test_without_the_relay_an_agent_registers_nothing(settings: Any) -> None:
    settings.AGENT_RELAY_PORT = 0

    with relaying("run-1", BROKER_TOKEN):
        assert not AgentRelay.objects.exists()
