from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from django.conf import settings

from secondshift.runner.agent_credentials import encrypt_agent_auth
from secondshift.runner.agent_protocol import AgentCheckInput, AgentInput, AgentOutput
from secondshift.runner.protocol import (
    CommandRequest,
    CommandResult,
    RunnerProtocolError,
    RunnerSessionSpec,
)


class HttpRunnerClient:
    """Trusted control-plane client for a separately deployed runner."""

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        timeout: int = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.strip() or not auth_token:
            raise ValueError("Runner URL and authentication token are required.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = httpx.Client(
            base_url=self.base_url,
            trust_env=False,
            transport=transport,
            headers={
                "Authorization": f"Bearer {auth_token}",
                "Accept": "application/json",
            },
        )

    def send(
        self, method: str, path: str, timeout: float | None = None, **kwargs: Any
    ) -> httpx.Response:
        """One call to the runner. Transport errors become RunnerProtocolError."""
        try:
            return self.session.request(
                method, path, timeout=timeout or self.timeout, **kwargs
            )
        except httpx.TimeoutException as error:
            raise RunnerProtocolError("The runner did not answer in time.") from error
        except httpx.HTTPError as error:
            raise RunnerProtocolError(
                "The runner service could not be reached."
            ) from error

    def upload_source(self, sha256: str, path: Path) -> None:
        """Send a run's snapshot as a raw stream, unless the runner has it."""
        if self.send("HEAD", f"/v1/sources/{sha256}").status_code == 204:
            return
        with path.open("rb") as stream:
            response = self.send(
                "PUT",
                f"/v1/sources/{sha256}",
                timeout=180,
                content=stream,
                # Django reads a body only up to its Content-Length.
                headers={
                    "Content-Type": "application/gzip",
                    "Content-Length": str(path.stat().st_size),
                },
            )
        if response.status_code != 204:
            self.response_payload(response)

    def start(self, spec: RunnerSessionSpec) -> str:
        response = self.send("POST", "/v1/sessions", json=asdict(spec))
        payload = self.response_payload(response)
        return str(payload["session_id"])

    def execute(self, session_id: str, request: CommandRequest) -> CommandResult:
        response = self.send(
            "POST",
            f"/v1/sessions/{session_id}/commands",
            timeout=max(self.timeout, request.timeout_seconds + 5),
            json=asdict(request),
        )
        return CommandResult(**self.response_payload(response))

    def collect(self, session_id: str, destination: Path) -> None:
        """Stop the sandbox and save its archive of changes and files."""
        try:
            with self.session.stream(
                "POST", f"/v1/sessions/{session_id}/collection", timeout=360
            ) as response:
                if response.status_code != 200:
                    response.read()
                    self.response_payload(response)
                    raise RunnerProtocolError("Runner returned an invalid response.")
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
        except httpx.TimeoutException as error:
            raise RunnerProtocolError("The runner did not answer in time.") from error
        except httpx.HTTPError as error:
            raise RunnerProtocolError(
                "The runner service could not be reached."
            ) from error

    def encrypted_auth(self, auth: str) -> str:
        """An agent login, encrypted so that only the runner can read it."""
        key = self.response_payload(self.send("GET", "/v1/agent-provisioning-key"))
        return encrypt_agent_auth(str(key["public_key"]), auth)

    def run_agent(self, session_id: str, request: AgentInput) -> AgentOutput:
        response = self.send(
            "POST",
            f"/v1/sessions/{session_id}/agent",
            timeout=request.timeout_seconds + 30,
            json=request.model_dump(mode="json")
            | {
                "auth": request.auth.get_secret_value(),
                "broker_token": request.broker_token.get_secret_value(),
            },
        )
        return AgentOutput.model_validate(self.response_payload(response))

    def check_agent(self, request: AgentCheckInput) -> AgentOutput:
        response = self.send(
            "POST",
            "/v1/agent-checks",
            # The runner stops the CLI after its own limit. This covers startup.
            timeout=28,
            json=request.model_dump(mode="json")
            | {"auth": request.auth.get_secret_value()},
        )
        return AgentOutput.model_validate(self.response_payload(response))

    def stop(self, session_id: str) -> None:
        response = self.send("DELETE", f"/v1/sessions/{session_id}")
        if response.status_code != 204:
            self.response_payload(response)

    @staticmethod
    def response_payload(response: httpx.Response) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as error:
            raise RunnerProtocolError(
                f"Runner answered HTTP {response.status_code}."
            ) from error
        if response.is_error:
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
