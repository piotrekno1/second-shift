from pathlib import PurePosixPath

from django.http import FileResponse, Http404, HttpRequest
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.config.constants import RunStart
from secondshift.jobs.access import job_for_request
from secondshift.repositories.access import repository_for_request
from secondshift.runs.access import run_for_request
from secondshift.runs.artifacts import artifact_storage
from secondshift.runs.deliveries import DeliveryQueueUnavailable, retry_delivery
from secondshift.runs.models import RunDelivery
from secondshift.runs.queue import cancel_queued_task
from secondshift.runs.reporting.queries import RunFilters, run_page
from secondshift.runs.schemas import (
    RunAgainSchema,
    RunDeliverySchema,
    RunDetailSchema,
    RunFileName,
    RunInProgressSchema,
    RunListQuery,
    RunPageSchema,
    RunSchema,
    TaskSubmissionSchema,
)
from secondshift.runs.serialization import (
    run_file_path,
    saved_file_path,
    serialize_delivery,
    serialize_run,
    serialize_run_detail,
    serialize_submission,
)
from secondshift.runs.services import RunInProgress, RunRefused, start_manual_run
from secondshift.runs.submissions import SkillSubmission, resubmission, submit_skill
from secondshift.workspaces.access import (
    Action,
    current_workspace,
    require,
)

router = Router(auth=django_auth, tags=["runs"])


@router.post("/runs/{run_id}/cancel", response=RunSchema)
def cancel_task(request: HttpRequest, run_id: int) -> RunSchema:
    require(request, Action.RUN_JOBS)
    run = run_for_request(request, run_id)
    try:
        return serialize_run(cancel_queued_task(run))
    except RunRefused as error:
        raise HttpError(409, str(error)) from error


@router.post("/runs/{run_id}/again", response=TaskSubmissionSchema)
def run_task_again(
    request: HttpRequest, run_id: int, payload: RunAgainSchema
) -> TaskSubmissionSchema:
    """Submit a task run's task again, as it was first submitted."""
    require(request, Action.RUN_JOBS)
    run = run_for_request(request, run_id)
    try:
        submission = resubmission(run, payload.idempotency_key)
        result = submit_skill(run.repository, submission, request.user, RunStart.MANUAL)
    except RunRefused as error:
        raise HttpError(409, str(error)) from error
    return serialize_submission(result.run, result.started)


@router.post(
    "/runs/{run_id}/deliveries/{delivery_id}/retry", response=RunDeliverySchema
)
def retry_run_delivery(
    request: HttpRequest, run_id: int, delivery_id: int
) -> RunDeliverySchema:
    require(request, Action.RUN_JOBS)
    run = run_for_request(request, run_id)
    try:
        return serialize_delivery(retry_delivery(run, delivery_id))
    except RunDelivery.DoesNotExist as error:
        raise Http404 from error
    except RunRefused as error:
        raise HttpError(409, str(error)) from error
    except DeliveryQueueUnavailable as error:
        raise HttpError(
            503, "The execution queue is unavailable. Try again shortly."
        ) from error


@router.get("/runs", response=RunPageSchema)
def runs(request: HttpRequest, query: Query[RunListQuery]) -> RunPageSchema:
    workspace = current_workspace(request)
    if not workspace:
        return RunPageSchema(
            items=[], count=0, page=1, page_size=query.page_size, status_counts={}
        )
    result = run_page(
        workspace,
        RunFilters(
            q=query.q,
            statuses=[str(status) for status in query.status],
            job_id=query.job_id,
            repository_id=query.repository_id,
            period=query.period,
            sort=query.sort,
        ),
        query.page,
        query.page_size,
    )
    return RunPageSchema(
        items=[serialize_run(run) for run in result.runs],
        count=result.count,
        page=result.page,
        page_size=result.page_size,
        status_counts=result.status_counts,
    )


@router.get("/runs/{run_id}", response=RunDetailSchema)
def run_detail(request: HttpRequest, run_id: int) -> RunDetailSchema:
    return serialize_run_detail(run_for_request(request, run_id))


def stored_file_response(
    path: str, filename: str, download: bool, content_type: str
) -> FileResponse:
    response = FileResponse(
        artifact_storage().open(path, "rb"),
        as_attachment=download,
        filename=filename,
        content_type=content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@router.get("/runs/{run_id}/files/{name}")
def run_file(
    request: HttpRequest, run_id: int, name: RunFileName, download: bool = False
) -> FileResponse:
    """Stream a stored run file through the configured storage backend.

    Always served as plain text, so a browser never renders repository or model
    content as HTML; the dashboard renders Markdown itself, sanitized.
    """
    run = run_for_request(request, run_id)
    path = run_file_path(run, name)
    if not path:
        raise Http404
    return stored_file_response(
        path, f"run-{run.pk}-{name}.md", download, "text/plain; charset=utf-8"
    )


@router.get("/runs/{run_id}/changes")
def run_changes_patch(
    request: HttpRequest, run_id: int, download: bool = False
) -> FileResponse:
    """The agent's file changes as a patch, always served as plain text."""
    run = run_for_request(request, run_id)
    path = run.collected.changes.path if run.collected else ""
    if not path:
        raise Http404
    return stored_file_response(
        path, f"run-{run.pk}-changes.patch", download, "text/plain; charset=utf-8"
    )


@router.get("/runs/{run_id}/saved-files/{path:name}")
def run_saved_file(request: HttpRequest, run_id: int, name: str) -> FileResponse:
    """One file the agent saved. Always a download, never rendered.

    The agent chose its name and content, so the browser must not open it as
    a page on this site.
    """
    run = run_for_request(request, run_id)
    path = saved_file_path(run, name)
    if not path:
        raise Http404
    return stored_file_response(
        path, PurePosixPath(name).name, True, "application/octet-stream"
    )


@router.post("/jobs/{job_id}/run", response={200: RunSchema, 409: RunInProgressSchema})
def run_job(
    request: HttpRequest, job_id: int
) -> RunSchema | Status[RunInProgressSchema]:
    job = job_for_request(request, job_id)
    require(request, Action.RUN_JOBS)
    try:
        run = start_manual_run(job, submitted_by=request.user)
    except RunInProgress as error:
        return Status(409, RunInProgressSchema(detail=str(error), run_id=error.run.pk))
    except RunRefused as error:
        raise HttpError(409, str(error)) from error
    return serialize_run(run)


@router.post("/tasks", response=TaskSubmissionSchema)
def submit_task(request: HttpRequest, payload: SkillSubmission) -> TaskSubmissionSchema:
    require(request, Action.RUN_JOBS)
    repository = repository_for_request(request, payload.repository_id)
    try:
        result = submit_skill(repository, payload, request.user, RunStart.MANUAL)
    except RunRefused as error:
        raise HttpError(409, str(error)) from error
    return serialize_submission(result.run, result.started)
