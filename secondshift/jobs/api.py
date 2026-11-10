from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.dao import workspace_connections
from secondshift.connections.serialization import serialize_connection
from secondshift.jobs.access import job_for_request
from secondshift.jobs.dao import job_requests
from secondshift.jobs.grants import GrantError, decide_grant
from secondshift.jobs.models import JOB_REMOVED
from secondshift.jobs.needs import capability_needs
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.readiness import execution_problems
from secondshift.jobs.scheduling import restart_schedules
from secondshift.jobs.schemas import (
    CapabilityNeedSchema,
    GrantDecisionSchema,
    JobDetailSchema,
    JobEnabledSchema,
    JobSchema,
    NeedJobSchema,
)
from secondshift.jobs.serialization import (
    serialize_job,
    serialize_job_detail,
    serialize_workspace_jobs,
)
from secondshift.workspaces.access import (
    Action,
    allows,
    current_workspace,
    require,
)
from secondshift.workspaces.limits import LimitError, save_overrides
from secondshift.workspaces.schemas import RunLimitsUpdateSchema
from secondshift.workspaces.serialization import limit_error_detail

router = Router(auth=django_auth, tags=["jobs"])


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


@router.get("/jobs", response=list[JobSchema])
def jobs(request: HttpRequest) -> list[JobSchema]:
    workspace = current_workspace(request)
    return serialize_workspace_jobs(workspace) if workspace else []


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
        save_overrides(job, payload.model_dump(exclude_unset=True))
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
        if job.removed_at is not None:
            raise HttpError(409, JOB_REMOVED)
        if not job.cron:
            raise HttpError(409, "This job has no schedule. Use Run now to run it.")
        if not job.repository.accepts_runs:
            raise HttpError(409, "Enable the repository before enabling this job.")
        plan = build_execution_plan(job)
        if not plan.ready:
            raise HttpError(409, " ".join(execution_problems(job.repository, plan)))
    turned_on = payload.enabled and not job.enabled
    job.enabled = payload.enabled
    job.save(update_fields=["enabled", "updated_at"])
    if turned_on:
        restart_schedules([job])
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
    capability = get_object_or_404(job_requests(job), pk=capability_id)
    connection = (
        None
        if payload.connection_id is None
        else get_object_or_404(
            workspace_connections(job.workspace_id), pk=payload.connection_id
        )
    )
    try:
        decide_grant(
            capability,
            payload.decision,
            connection,
            payload.allowed_tools,
            request.user,
            (
                None
                if payload.tool_rules is None
                else {
                    tool: {name: rule.dict() for name, rule in rules.items()}
                    for tool, rules in payload.tool_rules.items()
                }
            ),
        )
    except GrantError as error:
        raise HttpError(error.status, str(error)) from error
    return serialize_job(job_for_request(request, job_id))
