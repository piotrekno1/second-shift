from collections.abc import Callable
from typing import Any

import httpx
import pytest

from secondshift.runner_protocol.client import HttpRunnerClient
from secondshift.runner_protocol.sessions import CommandRequest, RunnerProtocolError
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

    with pytest.raises(RunnerProtocolError, match=message) as caught:
        client.stop("run-7")

    assert "runner:8002" not in str(caught.value)


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
