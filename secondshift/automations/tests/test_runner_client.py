from unittest.mock import Mock

import pytest

from secondshift.automations.runner_client import HttpRunnerClient
from secondshift.automations.runner_protocol import CommandRequest, RunnerProtocolError


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
