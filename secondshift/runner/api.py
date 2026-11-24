import base64
import hmac
from uuid import UUID

from django.conf import settings
from django.http import FileResponse, HttpRequest
from ninja import NinjaAPI, Status
from ninja.security import HttpBearer

from secondshift.runner.agent_credentials import provisioning_public_key
from secondshift.runner.agents import (
    agents_configured,
    check_agent,
    execute_agent,
    remove_agent_credentials,
)
from secondshift.runner.dao import has_sessions
from secondshift.runner.relay import (
    answer_call,
    collect_calls,
    on_relay_port,
    relay_enabled,
)
from secondshift.runner.schemas import (
    CommandInput,
    CommandOutput,
    SessionInput,
    SessionOutput,
)
from secondshift.runner.services import get_runner_service
from secondshift.runner.setup import run_setup
from secondshift.runner.sources import has_source, receive_source
from secondshift.runner_protocol.relay import (
    COLLECT_WAIT_SECONDS,
    MAX_ANSWER_BYTES,
    RELAY_OFF,
    RelayedAnswer,
)
from secondshift.runner_protocol.schemas import AgentCheckInput, AgentInput, AgentOutput
from secondshift.runner_protocol.sessions import (
    CommandRequest,
    RunnerProtocolError,
    RunnerSessionSpec,
)


class RunnerBearer(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> str | None:
        # Agents reach the relay port. The API is never served there.
        if on_relay_port(request):
            return None
        if hmac.compare_digest(token, settings.RUNNER_SERVICE_TOKEN):
            return token
        return None


api = NinjaAPI(
    title="SecondShift Runner API",
    version="1.0",
    auth=RunnerBearer(),
    urls_namespace="runner",
    docs_url=None,
    openapi_url=None,
)


@api.exception_handler(RunnerProtocolError)
def protocol_error(request: HttpRequest, error: RunnerProtocolError):
    return api.create_response(request, {"error": str(error)}, status=409)


@api.exception_handler(ValueError)
def invalid_protocol_input(request: HttpRequest, error: ValueError):
    return api.create_response(request, {"error": str(error)}, status=422)


@api.get("/-/health/", auth=None)
def health(request: HttpRequest) -> dict[str, bool]:
    has_sessions()
    return {"ok": True}


@api.get("/v1/environments", response=dict[str, dict[str, str] | str | bool])
def environments(request: HttpRequest) -> dict[str, dict[str, str] | str | bool]:
    """Each environment this runner offers, its image, the default's name, and
    whether it can run Claude Code and Codex.
    """
    offered = settings.RUNNER_ENVIRONMENTS
    return {
        "environments": offered.images,
        "default": offered.default,
        "agents": agents_configured(),
    }


@api.post("/v1/sessions", response={201: SessionOutput})
def start_session(request: HttpRequest, payload: SessionInput):
    spec = RunnerSessionSpec(**payload.model_dump())
    session_id = get_runner_service().start(spec)
    return Status(201, {"session_id": session_id})


@api.post("/v1/sessions/{session_id}/commands", response=CommandOutput)
def execute_command(request: HttpRequest, session_id: str, payload: CommandInput):
    command = CommandRequest(**{**payload.model_dump(), "argv": tuple(payload.argv)})
    return get_runner_service().execute(session_id, command)


@api.post("/v1/sessions/{session_id}/setup/{index}", response=CommandOutput)
def run_setup_command(request: HttpRequest, session_id: str, index: int):
    """Run one of the session's setup commands. The spec fixed them at start."""
    return run_setup(session_id, index)


@api.post("/v1/sessions/{session_id}/collection")
def collect_session(request: HttpRequest, session_id: str) -> FileResponse:
    """Stop the sandbox and return its changes and saved files as one archive."""
    archive = get_runner_service().collect(session_id)
    return FileResponse(archive.open("rb"), content_type="application/gzip")


@api.delete("/v1/sessions/{session_id}", response={204: None})
def stop_session(request: HttpRequest, session_id: str):
    get_runner_service().stop(session_id)
    return Status(204, None)


@api.api_operation(["HEAD"], "/v1/sources/{sha256}", response={204: None, 404: None})
def source_exists(request: HttpRequest, sha256: str) -> Status[None]:
    return Status(204 if has_source(sha256) else 404, None)


@api.put("/v1/sources/{sha256}", response={204: None})
def upload_source(request: HttpRequest, sha256: str) -> Status[None]:
    # Read the body as a stream, so a snapshot never sits in memory whole.
    length = request.headers.get("Content-Length")
    receive_source(sha256, request, int(length) if length else None)
    return Status(204, None)


@api.post("/v1/sessions/{session_id}/agent", response=AgentOutput)
def run_agent(
    request: HttpRequest, session_id: str, payload: AgentInput
) -> AgentOutput:
    return execute_agent(session_id, payload)


@api.get("/v1/sessions/{session_id}/agent/calls")
def agent_calls(request: HttpRequest, session_id: str):
    """The agent's tool calls nobody has collected, waiting briefly for one.

    Each call is handed out once.
    """
    if not relay_enabled():
        return api.create_response(request, {"error": RELAY_OFF}, status=404)
    calls = collect_calls(session_id, COLLECT_WAIT_SECONDS)
    return {
        "calls": [
            {"id": call.id, "body": base64.b64encode(call.body).decode()}
            for call in calls
        ]
    }


@api.post("/v1/sessions/{session_id}/agent/calls/{call_id}", response={204: None})
def answer_agent_call(
    request: HttpRequest, session_id: str, call_id: UUID, status: int
) -> Status[None]:
    """The broker's answer to a call, as the raw body and its HTTP status.

    An answer for a call that is gone, because the agent ended or stopped
    waiting, is dropped.
    """
    if not relay_enabled():
        return api.create_response(request, {"error": RELAY_OFF}, status=404)
    if not 100 <= status <= 599:
        raise ValueError("Invalid answer status.")
    # Read the stream: the runner's upload limit is far smaller than an answer.
    body = request.read(MAX_ANSWER_BYTES + 1)
    if len(body) > MAX_ANSWER_BYTES:
        raise ValueError("The answer is too large.")
    answer_call(session_id, call_id, RelayedAnswer(status, body))
    return Status(204, None)


@api.post("/v1/agent-checks", response=AgentOutput)
def run_agent_check(request: HttpRequest, payload: AgentCheckInput) -> AgentOutput:
    return check_agent(payload)


@api.delete("/v1/agent-credentials/{key}", response={204: None})
def revoke_agent_credentials(request: HttpRequest, key: str) -> Status[None]:
    remove_agent_credentials(key)
    return Status(204, None)


@api.get("/v1/agent-provisioning-key", response=dict[str, str])
def agent_provisioning_key(request: HttpRequest) -> dict[str, str]:
    return {"public_key": provisioning_public_key()}
