from datetime import datetime
from typing import Literal

from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ninja.security import django_auth
from pydantic import Field

from secondshift.automations.artifacts import artifact_storage, job_input_snapshot
from secondshift.automations.budgets import BUDGET_HEADLINE
from secondshift.automations.deadlines import TIMEOUT_HEADLINE
from secondshift.automations.dispatch import enqueue_run
from secondshift.automations.failures import describe_failure
from secondshift.automations.limits import (
    LimitError,
    LimitView,
    RunLimit,
    limit_views,
    save_overrides,
)
from secondshift.automations.models import (
    CapabilityRequest,
    Job,
    JobGrant,
    Run,
)
from secondshift.automations.planning import build_execution_plan
from secondshift.automations.run_queries import Period, RunFilters, RunSort, run_page
from secondshift.core.models import Workspace
from secondshift.core.services import workspace_for_user
from secondshift.gateway.usage import with_model_usage
from secondshift.integrations.external import changing_tools, external_access
from secondshift.integrations.models import Connection

router = Router(auth=django_auth, tags=["automations"])


class AccessSchema(Schema):
    ok: bool
    scope: str | None
    text: str


class CapabilitySchema(Schema):
    id: int
    binding: str
    kind: str
    requested_tools: list[str]
    required: bool
    grant_status: str
    connection_id: int | None
    connection_name: str | None
    # External tools only: what the job could reach through this binding.
    access: AccessSchema | None = None
    # Requested tools that may change data on the service.
    changing_tools: list[str] = Field(default_factory=list)


class JobSchema(Schema):
    id: int
    name: str
    repository_id: int
    repository: str
    skill_path: str
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    requested_capabilities: int
    grant_status: str
    capabilities: list[CapabilitySchema]


class JobEnabledSchema(Schema):
    enabled: bool


class GrantDecisionSchema(Schema):
    decision: Literal["approved", "denied"]
    connection_id: int | None = None
    allowed_tools: list[str] | None = None


class RunFailureSchema(Schema):
    label: str
    reason: str
    # The run stopped at one of its limits, which an owner can change.
    limit: bool = False


class LimitSchema(Schema):
    key: str
    label: str
    help: str
    unit: str
    value: int
    override: int | None
    inherited: int
    minimum: int
    maximum: int
    env: str
    maximum_env: str


class RunLimitsUpdateSchema(Schema):
    """Fields left out stay as they are. Null uses the inherited value."""

    max_turns: int | None = None
    max_tool_calls: int | None = None
    token_budget: int | None = None
    timeout_seconds: int | None = None


def serialize_limits(views: list[LimitView]) -> list[LimitSchema]:
    return [LimitSchema(**vars(view)) for view in views]


def limit_updates(payload: RunLimitsUpdateSchema) -> dict[str, int | None]:
    return payload.model_dump(exclude_unset=True)


class RunSchema(Schema):
    id: int
    job_id: int
    job: str
    repository_id: int
    repository: str
    status: str
    scheduled_for: datetime
    started_at: datetime | None = None
    finished_at: datetime | None
    summary: str
    plan_ready: bool
    commit_sha: str = ""
    model: str | None = None
    max_turns: int | None = None
    token_budget: int | None = None
    model_requests: int = 0
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    failure: RunFailureSchema | None = None
    has_input: bool = False
    has_output: bool = False
    model_tokens: int = 0
    model_cost_usd: float | None = None
    model_cost_partial: bool = False


class RunEventSchema(Schema):
    id: int
    level: str
    message: str
    created_at: datetime


RunFileName = Literal["input", "output"]


class RunFileSchema(Schema):
    name: RunFileName
    label: str
    filename: str
    size_bytes: int | None
    truncated: bool


class RunDetailSchema(RunSchema):
    job_key: str
    skill_path: str
    created_at: datetime
    exit_code: int | None
    model_provider: str | None
    output_excerpt: str
    output_truncated: bool
    output_size_bytes: int | None
    events: list[RunEventSchema]
    files: list[RunFileSchema]


class JobDetailSchema(JobSchema):
    key: str
    config_hash: str
    repository_revision: str
    repository_enabled: bool
    recent_runs: list[RunSchema]
    limits: list[LimitSchema]
    can_edit_limits: bool


class RunListQuery(Schema):
    q: str = Field("", max_length=200)
    status: list[Run.Status] = []
    job_id: int | None = None
    repository_id: int | None = None
    period: Period | None = None
    sort: RunSort = "newest"
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=100)


class RunPageSchema(Schema):
    items: list[RunSchema]
    count: int
    page: int
    page_size: int
    status_counts: dict[str, int]


def request_workspace(request: HttpRequest) -> Workspace | None:
    return workspace_for_user(request.user)


def job_for_request(request: HttpRequest, job_id: int) -> Job:
    return get_object_or_404(
        Job.objects.select_related("repository", "workspace").prefetch_related(
            "capability_requests__grant__connection"
        ),
        pk=job_id,
        workspace=request_workspace(request),
        repository__removed_at__isnull=True,
    )


def binding_connections(workspace_id: int) -> dict[str, Connection]:
    return {
        connection.binding_key: connection
        for connection in Connection.objects.filter(workspace_id=workspace_id).exclude(
            binding_key=""
        )
    }


def serialize_job(
    job: Job, connections: dict[str, Connection] | None = None
) -> JobSchema:
    """`connections` maps binding keys to connections, to share one query."""
    requests = list(job.capability_requests.all())
    if connections is None and requests:
        connections = binding_connections(job.workspace_id)
    capabilities: list[CapabilitySchema] = []
    for capability in requests:
        grant = getattr(capability, "grant", None)
        connection = (connections or {}).get(capability.binding)
        capabilities.append(
            CapabilitySchema(
                id=capability.pk,
                binding=capability.binding,
                kind=capability.kind,
                requested_tools=capability.requested_tools,
                required=capability.required,
                changing_tools=(
                    changing_tools(connection, capability.requested_tools)
                    if connection and connection.kind == capability.kind
                    else []
                ),
                access=(
                    AccessSchema(**vars(access))
                    if connection
                    and connection.kind == capability.kind
                    and (access := external_access(connection, job.repository))
                    else None
                ),
                grant_status=grant.status if grant else "unconfigured",
                connection_id=grant.connection_id if grant else None,
                connection_name=(
                    grant.connection.display_name
                    if grant and grant.connection_id
                    else None
                ),
            )
        )
    return JobSchema(
        id=job.id,
        name=job.name,
        repository_id=job.repository_id,
        repository=job.repository.display_name,
        skill_path=job.skill_path,
        cron=job.cron,
        timezone=job.timezone,
        enabled=job.enabled,
        next_run_at=job.next_run_at,
        requested_capabilities=len(requests),
        grant_status=job.capability_grant_status(),
        capabilities=capabilities,
    )


def run_limit(run: Run, limit: RunLimit) -> int | None:
    """A limit frozen into the run's plan. Older plans may not have it."""
    limits = (run.execution_plan.get("agent") or {}).get("limits") or {}
    value = limits.get(str(limit))
    return int(value) if isinstance(value, int) else None


def run_failure(run: Run) -> RunFailureSchema | None:
    if run.status == Run.Status.CANCELED:
        return RunFailureSchema(
            label="Canceled", reason=run.summary or "The run was canceled."
        )
    if run.status != Run.Status.FAILED:
        return None
    view = describe_failure(run.summary)
    return RunFailureSchema(
        label=view.label,
        reason=view.reason,
        limit=view.label in (BUDGET_HEADLINE, TIMEOUT_HEADLINE),
    )


def serialize_run(run: Run) -> RunSchema:
    return RunSchema(
        id=run.id,
        job_id=run.job_id,
        job=run.job.name,
        repository_id=run.job.repository_id,
        repository=run.job.repository.display_name,
        status=run.status,
        scheduled_for=run.scheduled_for,
        started_at=run.started_at,
        finished_at=run.finished_at,
        summary=run.summary,
        plan_ready=bool(run.execution_plan.get("ready", False)),
        commit_sha=run.commit_sha,
        model=(run.execution_plan.get("model") or {}).get("default_model") or None,
        max_turns=run_limit(run, RunLimit.MAX_TURNS),
        token_budget=run_limit(run, RunLimit.TOKEN_BUDGET),
        model_requests=getattr(run, "model_requests", None) or 0,
        model_input_tokens=getattr(run, "model_input_tokens", None) or 0,
        model_output_tokens=getattr(run, "model_output_tokens", None) or 0,
        failure=run_failure(run),
        has_input=bool(run.input_path),
        has_output=bool(run.output_path),
        model_tokens=getattr(run, "model_tokens", None) or 0,
        model_cost_usd=(
            float(cost) if (cost := getattr(run, "model_cost_usd", None)) else None
        ),
        model_cost_partial=bool(getattr(run, "unpriced_requests", 0)),
    )


def run_file_path(run: Run, name: RunFileName) -> str:
    return run.input_path if name == "input" else run.output_path


def run_files(run: Run) -> list[RunFileSchema]:
    files: list[RunFileSchema] = []
    if run.input_path:
        files.append(
            RunFileSchema(
                name="input",
                label="Input",
                filename=f"run-{run.pk}-input.md",
                size_bytes=run.input_size_bytes,
                truncated=False,
            )
        )
    if run.output_path:
        files.append(
            RunFileSchema(
                name="output",
                label="Output",
                filename=f"run-{run.pk}-output.md",
                size_bytes=run.output_size_bytes,
                truncated=run.output_truncated,
            )
        )
    return files


def serialize_run_detail(run: Run) -> RunDetailSchema:
    model = run.execution_plan.get("model") or {}
    return RunDetailSchema(
        **serialize_run(run).model_dump(),
        job_key=str(run.input_snapshot.get("job_key") or run.job.key),
        skill_path=str(run.input_snapshot.get("skill_path") or run.job.skill_path),
        created_at=run.created_at,
        exit_code=run.exit_code,
        model_provider=model.get("connection_name"),
        output_excerpt=run.output_excerpt,
        output_truncated=run.output_truncated,
        output_size_bytes=run.output_size_bytes,
        events=[
            RunEventSchema(
                id=event.id,
                level=event.level,
                message=event.message,
                created_at=event.created_at,
            )
            for event in run.events.order_by("created_at", "pk")
        ],
        files=run_files(run),
    )


def is_owner(request: HttpRequest, workspace: Workspace) -> bool:
    return workspace.owner_id == request.user.pk


def serialize_job_detail(job: Job, can_edit_limits: bool) -> JobDetailSchema:
    summary = serialize_job(job)
    # Aggregation drops Meta.ordering, so order explicitly: newest first.
    recent_runs = with_model_usage(
        job.runs.select_related("job__repository").order_by("-created_at", "-pk")
    )[:20]
    return JobDetailSchema(
        **summary.model_dump(),
        key=job.key,
        config_hash=job.config_hash,
        repository_revision=job.repository.config_sha,
        repository_enabled=job.repository.enabled,
        recent_runs=[serialize_run(run) for run in recent_runs],
        limits=serialize_limits(limit_views(job.workspace, job)),
        can_edit_limits=can_edit_limits,
    )


@router.get("/jobs", response=list[JobSchema])
def jobs(request: HttpRequest) -> list[JobSchema]:
    workspace = request_workspace(request)
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
    return serialize_job_detail(job, is_owner(request, job.workspace))


@router.post("/jobs/{job_id}/limits", response=JobDetailSchema)
def set_job_limits(
    request: HttpRequest, job_id: int, payload: RunLimitsUpdateSchema
) -> JobDetailSchema:
    """Change the run limits of one job. Runs already created keep theirs."""
    job = job_for_request(request, job_id)
    if not is_owner(request, job.workspace):
        raise HttpError(403, "Only the workspace owner can change run limits.")
    try:
        save_overrides(job, limit_updates(payload))
    except LimitError as error:
        raise HttpError(422, str(error)) from error
    return serialize_job_detail(job_for_request(request, job_id), True)


@router.post("/jobs/{job_id}/enabled", response=JobSchema)
def set_job_enabled(
    request: HttpRequest, job_id: int, payload: JobEnabledSchema
) -> JobSchema:
    job = job_for_request(request, job_id)
    if job.workspace.owner_id != request.user.pk:
        raise HttpError(403, "Only the workspace owner can enable jobs.")
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
@transaction.atomic
def review_job_capability(
    request: HttpRequest,
    job_id: int,
    capability_id: int,
    payload: GrantDecisionSchema,
) -> JobSchema:
    job = job_for_request(request, job_id)
    if job.workspace.owner_id != request.user.pk:
        raise HttpError(403, "Only the workspace owner can review capabilities.")
    capability = get_object_or_404(CapabilityRequest, pk=capability_id, job=job)
    connection = None
    if payload.connection_id is not None:
        connection = get_object_or_404(
            Connection, pk=payload.connection_id, workspace=job.workspace
        )
        if (
            connection.kind != capability.kind
            or connection.binding_key != capability.binding
        ):
            raise HttpError(422, "Connection kind and binding must match capability.")
        if connection.status != Connection.Status.CONNECTED:
            raise HttpError(409, "The selected connection is not connected.")
        if (
            payload.decision == JobGrant.Status.APPROVED
            and (access := external_access(connection, job.repository))
            and not access.ok
        ):
            raise HttpError(409, access.text)
    if payload.decision == JobGrant.Status.APPROVED and connection is None:
        raise HttpError(422, "Choose a connection before approving the capability.")

    requested_tools = set(capability.requested_tools)
    allowed_tools = set(
        capability.requested_tools
        if payload.allowed_tools is None
        else payload.allowed_tools
    )
    if payload.decision == JobGrant.Status.DENIED:
        allowed_tools = set()
    elif allowed_tools - requested_tools:
        raise HttpError(422, "Allowed tools must be requested by the job.")
    if payload.decision == JobGrant.Status.APPROVED:
        if allowed_tools != requested_tools:
            raise HttpError(422, "Approve every requested tool or deny the capability.")
        if connection and requested_tools - set(connection.available_tools):
            raise HttpError(409, "The connection lacks a requested tool.")

    grant = JobGrant.objects.filter(capability_request=capability).first() or JobGrant(
        capability_request=capability
    )
    grant.connection = connection
    grant.status = payload.decision
    grant.allowed_tools = sorted(allowed_tools)
    grant.reviewed_by = request.user
    grant.reviewed_at = timezone.now()
    grant.full_clean()
    grant.save()
    return serialize_job(job_for_request(request, job_id))


@router.get("/runs", response=RunPageSchema)
def runs(request: HttpRequest, query: Query[RunListQuery]) -> RunPageSchema:
    workspace = request_workspace(request)
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
        job__workspace=request_workspace(request),
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
    run = get_object_or_404(Run, pk=run_id, job__workspace=request_workspace(request))
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
