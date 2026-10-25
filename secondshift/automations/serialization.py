from secondshift.automations.api_limits import (
    serialize_limits,
)
from secondshift.automations.budgets import BUDGET_HEADLINE
from secondshift.automations.deadlines import TIMEOUT_HEADLINE
from secondshift.automations.failures import describe_failure
from secondshift.automations.limits import (
    RunLimit,
    limit_views,
)
from secondshift.automations.models import (
    Job,
    Run,
)
from secondshift.automations.schemas import (
    AccessSchema,
    CapabilitySchema,
    JobDetailSchema,
    JobSchema,
    RunDetailSchema,
    RunEventSchema,
    RunFailureSchema,
    RunFileName,
    RunFileSchema,
    RunSchema,
)
from secondshift.gateway.usage import with_model_usage
from secondshift.integrations.external import changing_tools, external_access
from secondshift.integrations.models import Connection


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
