from collections.abc import Callable

import httpx
import pytest

from secondshift.runner.client import HttpRunnerClient
from secondshift.runner.protocol import CommandRequest, RunnerProtocolError

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
