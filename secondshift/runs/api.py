from django.http import FileResponse, Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.gateway.usage import with_model_usage
from secondshift.jobs.access import job_for_request
from secondshift.jobs.planning import build_execution_plan
from secondshift.runs.artifacts import artifact_storage
from secondshift.runs.models import Run
from secondshift.runs.orchestration.dispatch import enqueue_run
from secondshift.runs.reporting.queries import RunFilters, run_page
from secondshift.runs.schemas import (
    RunDetailSchema,
    RunFileName,
    RunListQuery,
    RunPageSchema,
    RunSchema,
)
from secondshift.runs.serialization import (
    run_file_path,
    serialize_run,
    serialize_run_detail,
)
from secondshift.runs.services import create_run
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
    run = get_object_or_404(
        with_model_usage(Run.objects.select_related("job__repository")),
        pk=run_id,
        job__workspace=current_workspace(request),
    )
    return serialize_run_detail(run)


@router.get("/runs/{run_id}/files/{name}")
def run_file(
    request: HttpRequest, run_id: int, name: RunFileName, download: bool = False
) -> FileResponse:
    """Stream a stored run file through the configured storage backend.

    Always served as plain text, so a browser never renders repository or model
    content as HTML; the dashboard renders Markdown itself, sanitized.
    """
    run = get_object_or_404(Run, pk=run_id, job__workspace=current_workspace(request))
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


@router.post("/jobs/{job_id}/run", response=RunSchema)
def run_job(request: HttpRequest, job_id: int) -> RunSchema:
    job = job_for_request(request, job_id)
    require(request, Action.RUN_JOBS)
    # A manual run does not need the schedule. It still needs the repository.
    if not job.repository.enabled:
        raise HttpError(409, "Enable the repository before running its jobs.")
    execution_plan = build_execution_plan(job)
    if not execution_plan["ready"]:
        raise HttpError(
            409,
            "Connect a default model and approve all required capabilities first.",
        )
    run = create_run(job, timezone.now(), execution_plan=execution_plan)
    enqueue_run(run)
    return serialize_run(run)
