from dataclasses import asdict

import requests
from django.conf import settings

from secondshift.runner.protocol import (
    CommandRequest,
    CommandResult,
    RunnerBackend,
    RunnerProtocolError,
    RunnerSessionSpec,
)


class HttpRunnerClient(RunnerBackend):
    """Trusted control-plane client for a separately deployed runner."""

    def __init__(self, base_url: str, auth_token: str, timeout: int = 30) -> None:
        if not base_url.strip() or not auth_token:
            raise ValueError("Runner URL and authentication token are required.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {"Authorization": f"Bearer {auth_token}", "Accept": "application/json"}
        )

    def start(self, spec: RunnerSessionSpec) -> str:
        response = self.session.post(
            f"{self.base_url}/v1/sessions",
            json=asdict(spec),
            timeout=self.timeout,
        )
        payload = self.response_payload(response)
        return str(payload["session_id"])

    def execute(self, session_id: str, request: CommandRequest) -> CommandResult:
        response = self.session.post(
            f"{self.base_url}/v1/sessions/{session_id}/commands",
            json=asdict(request),
            timeout=max(self.timeout, request.timeout_seconds + 5),
        )
        return CommandResult(**self.response_payload(response))

    def stop(self, session_id: str) -> None:
        response = self.session.delete(
            f"{self.base_url}/v1/sessions/{session_id}", timeout=self.timeout
        )
        if response.status_code != 204:
            self.response_payload(response)

    @staticmethod
    def response_payload(response: requests.Response) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as error:
            raise RunnerProtocolError(
                f"Runner answered HTTP {response.status_code}."
            ) from error
        if not response.ok:
            message = payload.get("error") if isinstance(payload, dict) else None
            raise RunnerProtocolError(
                str(message)
                if message
                else f"Runner answered HTTP {response.status_code}."
            )
        if not isinstance(payload, dict):
            raise RunnerProtocolError("Runner returned an invalid response.")
        return payload


def get_runner_client() -> HttpRunnerClient:
    return HttpRunnerClient(
        base_url=settings.RUNNER_SERVICE_URL,
        auth_token=settings.RUNNER_SERVICE_TOKEN,
    )
