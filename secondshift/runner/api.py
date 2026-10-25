import hmac

from django.conf import settings
from django.http import HttpRequest
from ninja import NinjaAPI, Schema, Status
from ninja.security import HttpBearer
from pydantic import ConfigDict, Field

from secondshift.automations.runner_protocol import (
    CommandRequest,
    RunnerProtocolError,
    RunnerSessionSpec,
)
from secondshift.runner.models import RunnerSession
from secondshift.runner.services import get_runner_service


class RunnerBearer(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> str | None:
        if hmac.compare_digest(token, settings.RUNNER_SERVICE_TOKEN):
            return token
        return None


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid")


class SessionInput(StrictSchema):
    run_id: int = Field(gt=0)
    session_key: str = Field(min_length=32, max_length=32)
    repository: str = Field(min_length=3, max_length=300)
    commit_sha: str = Field(min_length=40, max_length=64)
    image: str = Field(min_length=1, max_length=300)


class SessionOutput(Schema):
    session_id: str


class CommandInput(StrictSchema):
    call_id: str = Field(min_length=1, max_length=200)
    argv: list[str] = Field(min_length=1, max_length=256)
    cwd: str = Field(default=".", max_length=1000)
    timeout_seconds: int = Field(default=120, ge=1, le=900)


class CommandOutput(Schema):
    call_id: str
    exit_code: int | None
    output: str
    timed_out: bool


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
    RunnerSession.objects.exists()
    return {"ok": True}


@api.post("/v1/sessions", response={201: SessionOutput})
def start_session(request: HttpRequest, payload: SessionInput):
    spec = RunnerSessionSpec(**payload.model_dump())
    session_id = get_runner_service().start(spec)
    return Status(201, {"session_id": session_id})


@api.post("/v1/sessions/{session_id}/commands", response=CommandOutput)
def execute_command(request: HttpRequest, session_id: str, payload: CommandInput):
    command = CommandRequest(**{**payload.model_dump(), "argv": tuple(payload.argv)})
    return get_runner_service().execute(session_id, command)


@api.delete("/v1/sessions/{session_id}", response={204: None})
def stop_session(request: HttpRequest, session_id: str):
    get_runner_service().stop(session_id)
    return Status(204, None)
