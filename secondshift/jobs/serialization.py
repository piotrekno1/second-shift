from secondshift.config.constants import GITHUB_BASE_URL
from secondshift.connections.dao import connections_by_binding
from secondshift.connections.execution import (
    ExecutionSelection,
    workspace_execution,
)
from secondshift.connections.external import changing_tools, external_access
from secondshift.connections.models import Connection
from secondshift.jobs.dao import active_jobs_with_grants
from secondshift.jobs.limits import job_limit_views
from secondshift.jobs.models import Job
from secondshift.jobs.schemas import (
    AccessSchema,
    CapabilitySchema,
    JobDetailSchema,
    JobSchema,
    LastRunSchema,
    OpenIssuesSchema,
    SkillInspectionSchema,
    SkillNeedSchema,
    ToolInputSchema,
)
from secondshift.jobs.skill_needs import MAX_INSPECTED_CHARS, SkillInspection
from secondshift.repositories.github import repository_ref
from secondshift.repositories.models import Repository
from secondshift.runs.dao import (
    job_with_last_run,
    newest_job_runs,
    with_last_run,
    with_model_usage,
)
from secondshift.runs.serialization import serialize_run
from secondshift.workspaces.models import Workspace
from secondshift.workspaces.serialization import serialize_limits


def last_run(job: Job) -> LastRunSchema | None:
    if not hasattr(job, "last_run_id"):
        job = job_with_last_run(job.pk)
    if job.last_run_id is None:
        return None
    return LastRunSchema(
        id=job.last_run_id, status=job.last_run_status, at=job.last_run_at
    )


def tool_inputs(connection: Connection, tools: list[str]) -> dict[str, ToolInputSchema]:
    definitions = connection.tool_definitions
    inputs = {}
    for tool in tools:
        schema = (definitions.get(tool) or {}).get("inputSchema") or {}
        inputs[tool] = ToolInputSchema(
            properties=schema.get("properties") or {},
            required=schema.get("required") or [],
        )
    return inputs


def serialize_job(
    job: Job,
    connections: dict[str, Connection] | None = None,
    execution: ExecutionSelection | None = None,
) -> JobSchema:
    """`connections` and `execution` are shared by every job in a list."""
    requests = list(job.capability_requests.all())
    if connections is None and requests:
        connections = connections_by_binding(job.workspace_id)
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
                    and (
                        access := external_access(
                            connection, repository_ref(job.repository)
                        )
                    )
                    else None
                ),
                tool_rules=grant.tool_rules if grant else {},
                tool_inputs=(
                    tool_inputs(connection, capability.requested_tools)
                    if connection
                    and connection.kind == capability.kind
                    and connection.kind in (Connection.Kind.MCP, Connection.Kind.API)
                    else {}
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
    execution = execution or workspace_execution(job.workspace)
    return JobSchema(
        id=job.id,
        execution_backend=execution.backend,
        execution_label=execution.label,
        execution_problem=execution.problem,
        name=job.name,
        repository_id=job.repository_id,
        repository=job.repository.display_name,
        skill_path=job.skill_path,
        skill_repository=(job.skill_source or {}).get("repository", ""),
        cron=job.cron,
        timezone=job.timezone,
        enabled=job.enabled,
        next_run_at=job.next_run_at,
        removed_at=job.removed_at,
        requested_capabilities=len(requests),
        grant_status=job.capability_grant_status(),
        capabilities=capabilities,
        last_run=last_run(job),
        triggers=job.triggers,
    )


def skill_url(job: Job) -> str:
    """The skill file on GitHub at the synced commit, or empty when there is none."""
    if source := job.skill_source:
        return (
            f"{GITHUB_BASE_URL}/{source['repository']}/blob/"
            f"{source['ref']}/{job.skill_path}"
        )
    repository = job.repository
    if (
        repository.source_type != Repository.SourceType.GITHUB
        or not repository.config_sha
    ):
        return ""
    return (
        f"{GITHUB_BASE_URL}/{repository.full_name}/blob/"
        f"{repository.config_sha}/{job.skill_path}"
    )


def serialize_job_detail(job: Job, can_edit_limits: bool) -> JobDetailSchema:
    summary = serialize_job(job)
    is_github = job.repository.source_type == Repository.SourceType.GITHUB
    # Aggregation drops Meta.ordering, so order explicitly: newest first.
    recent_runs = with_model_usage(newest_job_runs(job))[:20]
    return JobDetailSchema(
        **summary.model_dump(),
        key=job.key,
        config_hash=job.config_hash,
        history=job.history,
        subagents=job.subagents,
        open_pull_request=job.open_pull_request,
        can_open_pull_requests=is_github,
        pull_requests_allowed=job.repository.receives_pull_requests,
        open_issues=OpenIssuesSchema(**issues.model_dump())
        if (issues := job.issue_settings)
        else None,
        issues_allowed=job.repository.receives_issues,
        repository_revision=job.repository.config_sha,
        skill_revision=(job.skill_source or {}).get("ref") or job.repository.config_sha,
        repository_enabled=job.repository.enabled,
        skill_text=job.skill_text,
        skill_truncated=job.skill_truncated,
        skill_url=skill_url(job),
        recent_runs=[serialize_run(run) for run in recent_runs],
        limits=serialize_limits(job_limit_views(job)),
        can_edit_limits=can_edit_limits,
    )


def serialize_workspace_jobs(workspace: Workspace) -> list[JobSchema]:
    """Every job in the workspace whose repository was not removed."""
    queryset = with_last_run(active_jobs_with_grants(workspace.pk))
    connections = connections_by_binding(workspace.pk)
    # Every job runs on the workspace default, so resolve it once.
    execution = workspace_execution(workspace)
    return [serialize_job(item, connections, execution) for item in queryset]


def serialize_inspection(inspection: SkillInspection) -> SkillInspectionSchema:
    skill = inspection.skill
    return SkillInspectionSchema(
        repository=skill.repository,
        path=skill.path,
        commit_sha=skill.commit_sha,
        url=f"{GITHUB_BASE_URL}/{skill.repository}/blob/{skill.commit_sha}/{skill.path}",
        name=inspection.details.name,
        description=inspection.details.description,
        file_count=skill.file_count,
        size_bytes=skill.size_bytes,
        text=skill.text[:MAX_INSPECTED_CHARS],
        text_truncated=len(skill.text) > MAX_INSPECTED_CHARS,
        needs=[
            SkillNeedSchema(
                binding=need.binding,
                server=need.server,
                tools=need.tools,
                origin=need.origin,
                status=need.status,
                detail=need.detail,
            )
            for need in inspection.needs
        ],
    )
