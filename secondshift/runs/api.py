from django.http import FileResponse, Http404, HttpRequest
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.jobs.access import job_for_request
from secondshift.runs.access import run_for_request
from secondshift.runs.artifacts import artifact_storage
from secondshift.runs.reporting.queries import RunFilters, run_page
from secondshift.runs.schemas import (
    RunDetailSchema,
    RunFileName,
    RunInProgressSchema,
    RunListQuery,
    RunPageSchema,
    RunSchema,
)
from secondshift.runs.serialization import (
    run_file_path,
    serialize_run,
    serialize_run_detail,
)
from secondshift.runs.services import RunInProgress, RunRefused, start_manual_run
from secondshift.workspaces.access import (
    Action,
    current_workspace,
    require,
)

router = Router(auth=django_auth, tags=["runs"])


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
    response = FileResponse(
        artifact_storage().open(path, "rb"),
        as_attachment=download,
        filename=f"run-{run.pk}-{name}.md",
        content_type="text/plain; charset=utf-8",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@router.post("/jobs/{job_id}/run", response={200: RunSchema, 409: RunInProgressSchema})
def run_job(
    request: HttpRequest, job_id: int
) -> RunSchema | Status[RunInProgressSchema]:
    job = job_for_request(request, job_id)
    require(request, Action.RUN_JOBS)
    try:
        run = start_manual_run(job)
    except RunInProgress as error:
        return Status(409, RunInProgressSchema(detail=str(error), run_id=error.run.pk))
    except RunRefused as error:
        raise HttpError(409, str(error)) from error
    return serialize_run(run)
