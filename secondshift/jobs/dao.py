"""Queries and writes on jobs, capability requests and grants."""

from collections.abc import Collection, Iterable
from datetime import datetime
from typing import Any

from django.db.models import Count, QuerySet

from secondshift.connections.models import Connection
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace

# Fields a grant review changes. Bulk updates save only these.
GRANT_REVIEW_FIELDS = [
    "connection",
    "status",
    "allowed_tools",
    "reviewed_by",
    "reviewed_at",
    "updated_at",
]


# Jobs


def create_job(**fields: Any) -> Job:
    return Job.objects.create(**fields)


def create_jobs(jobs: list[Job]) -> None:
    Job.objects.bulk_create(jobs)


def active_jobs(
    workspace_id: int, repository_id: int | None = None, enabled: bool | None = None
) -> QuerySet[Job]:
    """The workspace's jobs whose repository was not removed, with the repository."""
    jobs = Job.objects.filter(
        workspace_id=workspace_id, repository__removed_at__isnull=True
    ).select_related("repository")
    if repository_id is not None:
        jobs = jobs.filter(repository_id=repository_id)
    if enabled is not None:
        jobs = jobs.filter(enabled=enabled)
    return jobs


def enabled_job_count(workspace_id: int) -> int:
    return active_jobs(workspace_id, enabled=True).count()


def find_active_job(workspace: Workspace | None, job_id: int) -> Job | None:
    """The job, with its repository, workspace, requests, grants and connections."""
    return (
        Job.objects.select_related("repository", "workspace")
        .prefetch_related("capability_requests__grant__connection")
        .filter(pk=job_id, workspace=workspace, repository__removed_at__isnull=True)
        .first()
    )


def jobs_by_id(job_ids: Iterable[int]) -> dict[int, Job]:
    """The jobs with these ids, with their repositories."""
    return Job.objects.select_related("repository").in_bulk(list(job_ids))


def schedulable_jobs() -> QuerySet[Job]:
    """Enabled jobs with a schedule in enabled repositories, oldest first."""
    return (
        Job.objects.filter(enabled=True, repository__enabled=True)
        .exclude(cron="")
        .order_by("pk")
    )


def scheduled_job(job_id: int) -> Job | None:
    """The job with its repository while both are enabled."""
    return (
        Job.objects.select_related("repository")
        .filter(pk=job_id, enabled=True, repository__enabled=True)
        .first()
    )


def save_next_runs(jobs: list[Job]) -> None:
    Job.objects.bulk_update(jobs, ["next_run_at"])


def lock_job(job: Job) -> None:
    """Lock the job for this transaction, so two starts wait for each other."""
    Job.objects.select_for_update().filter(pk=job.pk).first()


def retire_missing_jobs(
    repository: Repository, keys: Collection[str], now: datetime
) -> None:
    """Disable and mark removed the repository's jobs the manifest no longer names."""
    repository.jobs.exclude(key__in=keys).filter(removed_at__isnull=True).update(
        enabled=False, removed_at=now
    )


def enabled_repository_jobs(repository: Repository) -> list[Job]:
    return list(repository.jobs.filter(enabled=True))


def disable_repository_jobs(repository: Repository) -> None:
    repository.jobs.update(enabled=False)


def repository_jobs_with_grants(repository: Repository) -> QuerySet[Job]:
    return repository.jobs.prefetch_related("capability_requests__grant")


# Capability requests


def create_capability_requests(requests: list[CapabilityRequest]) -> None:
    CapabilityRequest.objects.bulk_create(requests)


def delete_capability_requests(request_ids: list[int]) -> None:
    CapabilityRequest.objects.filter(pk__in=request_ids).delete()


def repository_requests(repository: Repository) -> QuerySet[CapabilityRequest]:
    """Every request of the repository's jobs, with its grant."""
    return CapabilityRequest.objects.filter(job__repository=repository).select_related(
        "grant"
    )


def job_requests_with_grants(job: Job) -> QuerySet[CapabilityRequest]:
    """The job's requests with their grants and granted connections."""
    return job.capability_requests.select_related("grant__connection")


def find_request(
    repository: Repository, job_key: str, binding: str, kind: str
) -> CapabilityRequest | None:
    """The request of the repository's job for this binding, with its grant."""
    return (
        CapabilityRequest.objects.filter(
            job__repository=repository, job__key=job_key, binding=binding, kind=kind
        )
        .select_related("grant")
        .first()
    )


def workspace_requests(workspace: Workspace) -> QuerySet[CapabilityRequest]:
    """Requests of the workspace's active jobs, by binding then job name."""
    return (
        CapabilityRequest.objects.filter(
            job__workspace=workspace, job__repository__removed_at__isnull=True
        )
        .select_related("job__repository", "grant")
        .order_by("binding", "job__name")
    )


def active_requests() -> QuerySet[CapabilityRequest]:
    """Requests of jobs whose repository was not removed, with grant and job."""
    return CapabilityRequest.objects.filter(
        job__repository__removed_at__isnull=True
    ).select_related("grant", "job__repository")


def repository_active_requests(repository: Repository) -> list[CapabilityRequest]:
    return list(active_requests().filter(job__repository=repository))


def binding_active_requests(workspace_id: int, binding: str) -> list[CapabilityRequest]:
    """The workspace's active requests for this binding."""
    return list(
        active_requests().filter(job__workspace_id=workspace_id, binding=binding)
    )


# Grants


def create_grants(grants: list[JobGrant]) -> None:
    JobGrant.objects.bulk_create(grants)


def save_grant_reviews(grants: list[JobGrant]) -> None:
    JobGrant.objects.bulk_update(grants, GRANT_REVIEW_FIELDS)


def find_grant(request: CapabilityRequest) -> JobGrant | None:
    return JobGrant.objects.filter(capability_request=request).first()


def approved_tool_grant(
    job: Job, kind: str, binding: str, connection_id: int | None
) -> JobGrant | None:
    """The job's approved grant for this binding and connection, if any."""
    return (
        JobGrant.objects.select_related("connection", "capability_request")
        .filter(
            capability_request__job=job,
            capability_request__kind=kind,
            capability_request__binding=binding,
            connection_id=connection_id,
            status=JobGrant.Status.APPROVED,
        )
        .first()
    )


def approved_notification_targets(
    job_id: int, workspace_id: int, connection_ids: Collection[int]
) -> list[int]:
    """Of these connections, the ones still approved and connected for the job."""
    return sorted(
        JobGrant.objects.filter(
            capability_request__job_id=job_id,
            capability_request__kind=CapabilityRequest.Kind.NOTIFICATION,
            status=JobGrant.Status.APPROVED,
            connection_id__in=connection_ids,
            connection__status=Connection.Status.CONNECTED,
            connection__workspace_id=workspace_id,
        ).values_list("connection_id", flat=True)
    )


def approved_jobs_per_connection(workspace_id: int) -> dict[int, int]:
    """How many jobs each of the workspace's connections is approved for."""
    return {
        row["connection_id"]: row["jobs"]
        for row in JobGrant.objects.filter(
            connection__workspace_id=workspace_id, status=JobGrant.Status.APPROVED
        )
        .values("connection_id")
        .annotate(jobs=Count("capability_request__job", distinct=True))
        .order_by()
    }


def reopen_github_mcp_grants(repository: Repository) -> None:
    """Send the repository's approved GitHub MCP grants back for review."""
    JobGrant.objects.filter(
        capability_request__job__repository=repository,
        capability_request__kind=Connection.Kind.MCP,
        connection__provider=Connection.Provider.GITHUB,
        status=JobGrant.Status.APPROVED,
    ).update(status=JobGrant.Status.PENDING, reviewed_by=None, reviewed_at=None)


def release_connection_grants(connection: Connection) -> None:
    """Return the connection's grants to pending, without a connection."""
    connection.job_grants.update(
        connection=None,
        status=JobGrant.Status.PENDING,
        allowed_tools=[],
        reviewed_by=None,
        reviewed_at=None,
    )
