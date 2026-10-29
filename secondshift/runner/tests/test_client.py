from unittest.mock import Mock

import pytest

from secondshift.runner.client import HttpRunnerClient
from secondshift.runner.protocol import CommandRequest, RunnerProtocolError


def test_runner_client_uses_bearer_auth_without_environment_proxies() -> None:
    client = HttpRunnerClient("http://runner:8002/", "secret")

    assert client.base_url == "http://runner:8002"
    assert client.session.trust_env is False
    assert client.session.headers["Authorization"] == "Bearer secret"


def test_runner_client_does_not_repeat_or_mask_runner_conflicts() -> None:
    client = HttpRunnerClient("http://runner:8002", "secret")
    response = Mock(status_code=409, ok=False)
    response.json.return_value = {"error": "outcome unknown"}
    client.session.post = Mock(return_value=response)

    with pytest.raises(RunnerProtocolError, match="outcome unknown"):
        client.execute("run-7", CommandRequest("call-1", ("git", "status")))

    client.session.post.assert_called_once()


def test_source_upload_is_skipped_when_the_runner_has_it(tmp_path) -> None:
    snapshot = tmp_path / "source.tar.gz"
    snapshot.write_bytes(b"snapshot")
    client = HttpRunnerClient("http://runner:8002", "secret")
    client.session.head = Mock(return_value=Mock(status_code=204))
    client.session.put = Mock()

    client.upload_source("e" * 64, snapshot)

    client.session.put.assert_not_called()

    client.session.head = Mock(return_value=Mock(status_code=404))
    client.session.put = Mock(return_value=Mock(status_code=204))

    client.upload_source("e" * 64, snapshot)

    client.session.put.assert_called_once()
    assert client.session.put.call_args.args == (
        f"http://runner:8002/v1/sources/{'e' * 64}",
    )
