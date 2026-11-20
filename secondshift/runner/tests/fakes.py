import json
from typing import Any

import httpx

from secondshift.runner.agent_credentials import provisioning_public_key
from secondshift.runner_protocol.client import HttpRunnerClient
from secondshift.runner_protocol.environments import RunnerEnvironments


def sandboxes(default: str, **others: str) -> RunnerEnvironments:
    """A runner's sandboxes: python, the default, on this image, and others."""
    return RunnerEnvironments(images={"python": default, **others}, default="python")


def agent_runner(
    result: dict[str, Any] | Exception,
) -> tuple[HttpRunnerClient, list[dict[str, Any]]]:
    """A real client whose runner gives out the provisioning key and answers
    every agent request with result. Also returns each request it was sent."""
    sent: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        sent.append({"method": request.method, "path": request.url.path, "json": body})
        if request.url.path == "/v1/agent-provisioning-key":
            return httpx.Response(200, json={"public_key": provisioning_public_key()})
        if isinstance(result, Exception):
            raise result
        return httpx.Response(200, json=result)

    client = HttpRunnerClient(
        "http://runner.test", "runner-token", transport=httpx.MockTransport(handle)
    )
    return client, sent
