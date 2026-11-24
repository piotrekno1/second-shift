from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
from django.conf import settings

from secondshift.runner_protocol.credentials import (
    encrypt_agent_auth,
    key_fingerprint,
    same_key,
)
from secondshift.runner_protocol.environments import RunnerEnvironments
from secondshift.runner_protocol.schemas import AgentCheckInput, AgentInput, AgentOutput
from secondshift.runner_protocol.sessions import (
    MAX_SETUP_SECONDS,
    CommandRequest,
    CommandResult,
    RunnerProtocolError,
    RunnerSessionSpec,
    RunnerUnreachable,
)


class HttpRunnerClient:
    """Trusted control-plane client for a separately deployed runner."""

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        timeout: int = 30,
        transport: httpx.BaseTransport | None = None,
        provisioning_key: str = "",
        first_key: Callable[[str], str] | None = None,
    ) -> None:
        if not base_url.strip() or not auth_token:
            raise ValueError("Runner URL and authentication token are required.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # The key registered for this runner. Logins are sealed only to it.
        self.provisioning_key = provisioning_key
        # Called with the runner's key while none is pinned. Returns the key to
        # trust, so a first contact pins it once.
        self.first_key = first_key
        self.session = httpx.Client(
            base_url=self.base_url,
            trust_env=False,
            transport=transport,
            headers={
                "Authorization": f"Bearer {auth_token}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        """Close the connection pool. The client sends nothing after this."""
        self.session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def send(
        self, method: str, path: str, timeout: float | None = None, **kwargs: Any
    ) -> httpx.Response:
        """One call to the runner. Transport errors become RunnerProtocolError."""
        try:
            return self.session.request(
                method, path, timeout=timeout or self.timeout, **kwargs
            )
        except httpx.TimeoutException as error:
            raise RunnerUnreachable("The runner did not answer in time.") from error
        except httpx.HTTPError as error:
            raise RunnerUnreachable(
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

    def environments(self, timeout: float | None = None) -> RunnerEnvironments:
        """Each environment the runner offers, its image, and the default."""
        payload = self.response_payload(
            self.send("GET", "/v1/environments", timeout=timeout)
        )
        images = payload.get("environments")
        default = payload.get("default")
        agents = payload.get("agents")
        if (
            not isinstance(images, dict)
            or not isinstance(default, str)
            or not isinstance(agents, bool | None)
        ):
            raise RunnerProtocolError("Runner returned an invalid response.")
        return RunnerEnvironments(
            images={str(name): str(image) for name, image in images.items()},
            default=default,
            agents=agents,
        )

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

    def setup(self, session_id: str, index: int) -> CommandResult:
        """Run the session's setup command at this index, before the agent."""
        response = self.send(
            "POST",
            f"/v1/sessions/{session_id}/setup/{index}",
            # The runner stops a setup command after at most 900 seconds.
            timeout=MAX_SETUP_SECONDS + 60,
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

    def current_provisioning_key(self, timeout: float | None = None) -> str:
        """The public key the runner answers with now."""
        payload = self.response_payload(
            self.send("GET", "/v1/agent-provisioning-key", timeout=timeout)
        )
        key = payload.get("public_key")
        if not isinstance(key, str):
            raise RunnerProtocolError("Runner returned an invalid response.")
        try:
            key_fingerprint(key)
        except ValueError:
            raise RunnerProtocolError("Runner returned an invalid key.") from None
        return key

    def encrypted_auth(self, auth: str) -> str:
        """An agent login, encrypted so that only the runner can read it.

        With a registered key, a different answer is refused: whoever answered
        could read the login.
        """
        key = self.current_provisioning_key()
        if not self.provisioning_key and self.first_key is not None:
            self.provisioning_key = self.first_key(key)
        if self.provisioning_key and not same_key(key, self.provisioning_key):
            raise RunnerProtocolError(
                "The runner's provisioning key does not match the one registered "
                "for it. No login was sent."
            )
        return encrypt_agent_auth(key, auth)

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
