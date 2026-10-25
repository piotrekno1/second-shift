from django.http import FileResponse, Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.automations.api_limits import (
    limit_error_detail,
    limit_updates,
)
from secondshift.automations.artifacts import artifact_storage, job_input_snapshot
from secondshift.automations.dispatch import enqueue_run
from secondshift.automations.grants import GrantError, decide_grant
from secondshift.automations.limits import (
    LimitError,
    save_overrides,
)
from secondshift.automations.models import (
    CapabilityRequest,
    Job,
    Run,
)
from secondshift.automations.needs import capability_needs
from secondshift.automations.planning import build_execution_plan
from secondshift.automations.run_queries import RunFilters, run_page
from secondshift.automations.schemas import (
    CapabilityNeedSchema,
    GrantDecisionSchema,
    JobDetailSchema,
    JobEnabledSchema,
    JobSchema,
    NeedJobSchema,
    RunDetailSchema,
    RunFileName,
    RunLimitsUpdateSchema,
    RunListQuery,
    RunPageSchema,
    RunSchema,
)
from secondshift.automations.serialization import (
    binding_connections,
    run_file_path,
    serialize_job,
    serialize_job_detail,
    serialize_run,
    serialize_run_detail,
)
from secondshift.gateway.usage import with_model_usage
from secondshift.integrations.models import Connection
from secondshift.integrations.serialization import serialize_connection
from secondshift.workspaces.access import (
    Action,
    allows,
    current_workspace,
    require,
)

router = Router(auth=django_auth, tags=["automations"])


@router.get("/capability-needs", response=list[CapabilityNeedSchema], auth=django_auth)
def needs(request: HttpRequest) -> list[CapabilityNeedSchema]:
    workspace = current_workspace(request)
    if not workspace:
        return []
    return [
        CapabilityNeedSchema(
            binding=need.binding,
            kind=need.kind,
            required=need.required,
            requested_tools=need.requested_tools,
            status=need.status,
            detail=need.detail,
            connection=(
                serialize_connection(need.connection) if need.connection else None
            ),
            jobs=[
                NeedJobSchema(
                    **{
                        **vars(job),
                        "access": vars(job.access) if job.access else None,
                    }
                )
                for job in need.jobs
            ],
        )
        for need in capability_needs(workspace)
    ]


def job_for_request(request: HttpRequest, job_id: int) -> Job:
    return get_object_or_404(
        Job.objects.select_related("repository", "workspace").prefetch_related(
            "capability_requests__grant__connection"
        ),
        pk=job_id,
        workspace=current_workspace(request),
        repository__removed_at__isnull=True,
    )


@router.get("/jobs", response=list[JobSchema])
def jobs(request: HttpRequest) -> list[JobSchema]:
    workspace = current_workspace(request)
    if not workspace:
        return []
    queryset = Job.objects.filter(
        workspace=workspace,
        repository__removed_at__isnull=True,
    ).select_related("repository")
    connections = binding_connections(workspace.pk)
    return [
        serialize_job(item, connections)
        for item in queryset.prefetch_related("capability_requests__grant__connection")
    ]


@router.get("/jobs/{job_id}", response=JobDetailSchema)
def job_detail(request: HttpRequest, job_id: int) -> JobDetailSchema:
    job = job_for_request(request, job_id)
    return serialize_job_detail(job, allows(request, Action.CHANGE_RUN_LIMITS))


@router.post("/jobs/{job_id}/limits", response=JobDetailSchema)
def set_job_limits(
    request: HttpRequest, job_id: int, payload: RunLimitsUpdateSchema
) -> JobDetailSchema:
    """Change the run limits of one job. Runs already created keep theirs."""
    job = job_for_request(request, job_id)
    require(request, Action.CHANGE_RUN_LIMITS)
    try:
        save_overrides(job, limit_updates(payload))
    except LimitError as error:
        raise HttpError(422, limit_error_detail(request, error)) from error
    return serialize_job_detail(job_for_request(request, job_id), True)


@router.post("/jobs/{job_id}/enabled", response=JobSchema)
def set_job_enabled(
    request: HttpRequest, job_id: int, payload: JobEnabledSchema
) -> JobSchema:
    job = job_for_request(request, job_id)
    require(request, Action.ENABLE_JOBS)
    if payload.enabled:
        if not job.cron:
            raise HttpError(409, "This job has no schedule. Use Run now to run it.")
        if not job.repository.enabled:
            raise HttpError(409, "Enable the repository before enabling this job.")
        if not build_execution_plan(job)["ready"]:
            raise HttpError(
                409,
                "Connect a default model and approve all required capabilities first.",
            )
    job.enabled = payload.enabled
    job.save(update_fields=["enabled", "updated_at"])
    return serialize_job(job_for_request(request, job_id))


@router.post("/jobs/{job_id}/capabilities/{capability_id}/grant", response=JobSchema)
def review_job_capability(
    request: HttpRequest,
    job_id: int,
    capability_id: int,
    payload: GrantDecisionSchema,
) -> JobSchema:
    job = job_for_request(request, job_id)
    require(request, Action.REVIEW_CAPABILITIES)
    capability = get_object_or_404(CapabilityRequest, pk=capability_id, job=job)
    connection = (
        None
        if payload.connection_id is None
        else get_object_or_404(
            Connection, pk=payload.connection_id, workspace=job.workspace
        )
    )
    try:
        decide_grant(
            capability,
            payload.decision,
            connection,
            payload.allowed_tools,
            request.user,
        )
    except GrantError as error:
        raise HttpError(error.status, str(error)) from error
    return serialize_job(job_for_request(request, job_id))


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
    run = Run.objects.create(
        job=job,
        commit_sha=job.repository.config_sha,
        scheduled_for=timezone.now(),
        input_snapshot=job_input_snapshot(job),
        execution_plan=execution_plan,
    )
    enqueue_run(run)
    return serialize_run(run)
