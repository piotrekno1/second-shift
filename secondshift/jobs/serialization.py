from django.db.models import OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce

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
    LastRunSchema,
)
from secondshift.runs.models import Run
from secondshift.runs.serialization import serialize_run
from secondshift.workspaces.models import Workspace


def with_last_run(jobs: QuerySet[Job]) -> QuerySet[Job]:
    """Annotate each job with its newest run, so a list needs no query per job."""
    newest = Run.objects.filter(job=OuterRef("pk")).order_by("-created_at", "-pk")
    return jobs.annotate(
        last_run_id=Subquery(newest.values("pk")[:1]),
        last_run_status=Subquery(newest.values("status")[:1]),
        last_run_at=Subquery(
            newest.annotate(at=Coalesce("finished_at", "created_at")).values("at")[:1]
        ),
    )


def last_run(job: Job) -> LastRunSchema | None:
    if not hasattr(job, "last_run_id"):
        job = with_last_run(Job.objects.filter(pk=job.pk)).get()
    if job.last_run_id is None:
        return None
    return LastRunSchema(
        id=job.last_run_id, status=job.last_run_status, at=job.last_run_at
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
        last_run=last_run(job),
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
        history=job.history,
        repository_revision=job.repository.config_sha,
        repository_enabled=job.repository.enabled,
        skill_text=job.skill_text,
        skill_truncated=job.skill_truncated,
        recent_runs=[serialize_run(run) for run in recent_runs],
        limits=serialize_limits(limit_views(job.workspace, job)),
        can_edit_limits=can_edit_limits,
    )


def serialize_workspace_jobs(workspace: Workspace) -> list[JobSchema]:
    """Every job in the workspace whose repository was not removed."""
    queryset = with_last_run(
        Job.objects.filter(
            workspace=workspace,
            repository__removed_at__isnull=True,
        ).select_related("repository")
    )
    connections = binding_connections(workspace.pk)
    return [
        serialize_job(item, connections)
        for item in queryset.prefetch_related("capability_requests__grant__connection")
    ]
