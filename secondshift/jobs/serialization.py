from secondshift.connections.external import changing_tools, external_access
from secondshift.connections.models import Connection
from secondshift.gateway.usage import with_model_usage
from secondshift.jobs.api_limits import serialize_limits
from secondshift.jobs.limits import limit_views
from secondshift.jobs.models import Job
from secondshift.jobs.schemas import (
    AccessSchema,
    CapabilitySchema,
    JobDetailSchema,
    JobSchema,
)
from secondshift.runs.serialization import serialize_run


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
