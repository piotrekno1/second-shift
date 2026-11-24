import json
from typing import Any, Self
from unittest.mock import MagicMock

import httpx

from secondshift.runner.agent_credentials import provisioning_public_key
from secondshift.runner_protocol.client import HttpRunnerClient
from secondshift.runner_protocol.environments import RunnerEnvironments


class ClosableFake:
    """Lets a fake runner client stand where callers write `with client:`."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def closable_mock() -> MagicMock:
    """A mock runner client that is its own context."""
    client = MagicMock()
    client.__enter__.return_value = client
    return client


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
