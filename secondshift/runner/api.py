import hmac

from django.conf import settings
from django.http import HttpRequest
from ninja import NinjaAPI, Status
from ninja.security import HttpBearer

from secondshift.runner.models import RunnerSession
from secondshift.runner.protocol import (
    CommandRequest,
    RunnerProtocolError,
    RunnerSessionSpec,
)
from secondshift.runner.repositories import import_bundle
from secondshift.runner.schemas import (
    CommandInput,
    CommandOutput,
    RepositoryImportInput,
    SessionInput,
    SessionOutput,
)
from secondshift.runner.services import get_runner_service
from secondshift.runner.sources import has_source, receive_source


class RunnerBearer(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> str | None:
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


@api.api_operation(["HEAD"], "/v1/sources/{sha256}", response={204: None, 404: None})
def source_exists(request: HttpRequest, sha256: str) -> Status[None]:
    return Status(204 if has_source(sha256) else 404, None)


@api.put("/v1/sources/{sha256}", response={204: None})
def upload_source(request: HttpRequest, sha256: str) -> Status[None]:
    # Read the body as a stream, so a snapshot never sits in memory whole.
    length = request.headers.get("Content-Length")
    receive_source(sha256, request, int(length) if length else None)
    return Status(204, None)


@api.post("/v1/repositories", response={204: None})
def import_repository(
    request: HttpRequest, payload: RepositoryImportInput
) -> Status[None]:
    import_bundle(payload.repository, payload.commit_sha, payload.bundle)
    return Status(204, None)
