import base64
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from secondshift.runner_protocol.client import HttpRunnerClient
from secondshift.runner_protocol.relay import RelayedAnswer, RelayedCall
from secondshift.runner_protocol.sessions import (
    CommandRequest,
    RunnerProtocolError,
    RunnerUnreachable,
)
from secondshift.runner_protocol.tests.keys import public_key_pem

Handler = Callable[[httpx.Request], httpx.Response]


def runner(handler: Handler) -> tuple[HttpRunnerClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        return handler(request)

    client = HttpRunnerClient(
        "http://runner:8002/", "secret", transport=httpx.MockTransport(record)
    )
    return client, seen


def test_runner_client_uses_bearer_auth_without_environment_proxies() -> None:
    client = HttpRunnerClient("http://runner:8002/", "secret")

    assert client.base_url == "http://runner:8002"
    assert client.session.trust_env is False
    assert client.session.headers["Authorization"] == "Bearer secret"


def test_runner_client_does_not_repeat_or_mask_runner_conflicts() -> None:
    client, seen = runner(
        lambda request: httpx.Response(409, json={"error": "outcome unknown"})
    )

    with pytest.raises(RunnerProtocolError, match="outcome unknown"):
        client.execute("run-7", CommandRequest("call-1", ("git", "status")))

    assert len(seen) == 1


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (httpx.ConnectError("http://runner:8002 refused"), "could not be reached"),
        (httpx.ReadTimeout("read timed out"), "did not answer in time"),
    ],
)
def test_transport_errors_are_named_without_their_text(
    error: Exception, message: str
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise error

    client, _ = runner(fail)

    with pytest.raises(RunnerUnreachable, match=message) as caught:
        client.stop("run-7")

    assert "runner:8002" not in str(caught.value)


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_proxy_answering_for_a_stopped_runner_is_unreachable(status: int) -> None:
    # Caddy in front of a stopped runner answers for it.
    client, _ = runner(lambda request: httpx.Response(status, text="Bad Gateway"))

    with pytest.raises(RunnerUnreachable, match=f"HTTP {status}"):
        client.stop("run-7")


def test_source_upload_is_skipped_when_the_runner_has_it(tmp_path) -> None:
    snapshot = tmp_path / "source.tar.gz"
    snapshot.write_bytes(b"snapshot")
    client, seen = runner(lambda request: httpx.Response(204))

    client.upload_source("e" * 64, snapshot)

    assert [request.method for request in seen] == ["HEAD"]


def test_source_upload_sends_the_snapshot_with_its_length(tmp_path) -> None:
    snapshot = tmp_path / "source.tar.gz"
    snapshot.write_bytes(b"snapshot")
    client, seen = runner(
        lambda request: httpx.Response(404 if request.method == "HEAD" else 204)
    )

    client.upload_source("e" * 64, snapshot)

    upload = seen[-1]
    assert upload.method == "PUT"
    assert str(upload.url) == f"http://runner:8002/v1/sources/{'e' * 64}"
    assert upload.headers["Content-Length"] == "8"
    assert upload.content == b"snapshot"


def test_collect_saves_the_archive_the_runner_sends(tmp_path) -> None:
    client, seen = runner(lambda request: httpx.Response(200, content=b"archive"))
    destination = tmp_path / "collection.tar.gz"

    client.collect("run-7", destination)

    assert destination.read_bytes() == b"archive"
    assert seen[0].url.path == "/v1/sessions/run-7/collection"


def test_collect_reports_the_runner_error(tmp_path) -> None:
    client, _ = runner(
        lambda request: httpx.Response(409, json={"error": "files are gone"})
    )

    with pytest.raises(RunnerProtocolError, match="files are gone"):
        client.collect("run-7", tmp_path / "collection.tar.gz")


def test_the_client_closes_its_connections_when_the_block_ends() -> None:
    with HttpRunnerClient("http://runner:8002", "t" * 32) as client:
        assert not client.session.is_closed

    assert client.session.is_closed


PINNED = public_key_pem()
IMPOSTOR = public_key_pem()


def answering(key: str, **pinning: Any) -> HttpRunnerClient:
    """A client whose runner answers the provisioning key with this key."""
    return HttpRunnerClient(
        "https://runner:8002/",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"public_key": key})
        ),
        **pinning,
    )


def test_a_login_is_sealed_to_the_pinned_key() -> None:
    sealed = answering(PINNED, provisioning_key=PINNED).encrypted_auth("login")

    assert "login" not in sealed


def test_a_login_is_never_sealed_to_a_key_that_is_not_pinned() -> None:
    # Whoever answers in the runner's place can't read the login.
    client = answering(IMPOSTOR, provisioning_key=PINNED)

    with pytest.raises(RunnerProtocolError, match="does not match"):
        client.encrypted_auth("login")


def test_first_contact_pins_the_key_once() -> None:
    pinned: list[str] = []

    def first_key(key: str) -> str:
        pinned.append(key)
        return key

    client = answering(PINNED, first_key=first_key)
    client.encrypted_auth("login")
    client.encrypted_auth("login")

    assert pinned == [PINNED]
    assert client.provisioning_key == PINNED


def test_first_contact_checks_against_a_key_pinned_meanwhile() -> None:
    # Another process pinned the runner's real key first.
    client = answering(IMPOSTOR, first_key=lambda key: PINNED)

    with pytest.raises(RunnerProtocolError, match="does not match"):
        client.encrypted_auth("login")


def test_a_runner_answering_with_no_valid_key_is_refused() -> None:
    with pytest.raises(RunnerProtocolError, match="invalid key"):
        answering("not a key").encrypted_auth("login")


@pytest.mark.parametrize(
    ("answer", "agents"),
    [({"agents": True}, True), ({"agents": False}, False), ({}, None)],
)
def test_the_client_reads_whether_the_runner_can_run_agents(
    answer: dict[str, Any], agents: bool | None
) -> None:
    client, _ = runner(
        lambda request: httpx.Response(
            200, json={"environments": {"python": "p"}, "default": "python"} | answer
        )
    )

    assert client.environments().agents is agents


def test_the_client_refuses_an_invalid_agents_answer() -> None:
    client, _ = runner(
        lambda request: httpx.Response(
            200,
            json={"environments": {}, "default": "python", "agents": "yes"},
        )
    )

    with pytest.raises(RunnerProtocolError, match="invalid response"):
        client.environments()


def test_the_client_collects_relayed_calls() -> None:
    call_id = "6f1c1c2e-8f43-4d5c-9a8e-3c0b7f0d2a11"
    client, seen = runner(
        lambda request: httpx.Response(
            200,
            json={"calls": [{"id": call_id, "body": base64.b64encode(b"{}").decode()}]},
        )
    )

    assert client.agent_calls("run-1") == [RelayedCall(call_id, b"{}")]
    assert seen[0].url.path == "/v1/sessions/run-1/agent/calls"


def test_a_runner_that_does_not_relay_gives_no_calls() -> None:
    client, _ = runner(
        lambda request: httpx.Response(404, json={"detail": "Not Found"})
    )

    assert client.agent_calls("run-1") is None


@pytest.mark.parametrize(
    "calls",
    [
        {"calls": "nope"},
        {"calls": [{"id": "not-a-uuid", "body": ""}]},
        {"calls": [{"id": "6f1c1c2e-8f43-4d5c-9a8e-3c0b7f0d2a11", "body": "%%"}]},
    ],
)
def test_the_client_refuses_invalid_relayed_calls(calls: dict[str, Any]) -> None:
    client, _ = runner(lambda request: httpx.Response(200, json=calls))

    with pytest.raises(RunnerProtocolError, match="invalid response"):
        client.agent_calls("run-1")


def test_the_client_sends_an_answer_as_its_raw_body() -> None:
    client, seen = runner(lambda request: httpx.Response(204))

    client.answer_agent_call("run-1", "call-1", RelayedAnswer(202, b""))
    client.answer_agent_call("run-1", "call-2", RelayedAnswer(200, b'{"id": 1}'))

    assert seen[0].url.path == "/v1/sessions/run-1/agent/calls/call-1"
    assert seen[0].url.params["status"] == "202"
    assert seen[1].content == b'{"id": 1}'
