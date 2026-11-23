from django.http import HttpRequest

from secondshift.github import repos
from secondshift.github.errors import GitHubError
from secondshift.jobs.autosync import server_syncs
from secondshift.jobs.dao import repository_jobs_with_grants
from secondshift.repositories.github.access import github_access
from secondshift.repositories.local import (
    LocalRepositoryError,
    locate_local_repository,
)
from secondshift.repositories.models import Repository
from secondshift.repositories.schemas import (
    RepositoryDetailSchema,
    RepositoryJobSchema,
    RepositorySchema,
    RepositoryValidationSchema,
)
from secondshift.repositories.status import ValidationStatus, source_status
from secondshift.workspaces.access import (
    Action,
    allows,
)


def visible_local_path(request: HttpRequest, repository: Repository) -> str | None:
    if repository.source_type == Repository.SourceType.LOCAL and allows(
        request, Action.VIEW_LOCAL_PATHS
    ):
        return repository.local_path
    return None


def serialize_repository(
    request: HttpRequest,
    repository: Repository,
    job_count: int,
) -> RepositorySchema:
    return RepositorySchema(
        id=repository.pk,
        full_name=repository.full_name,
        display_name=repository.display_name,
        default_branch=repository.default_branch,
        enabled=repository.enabled,
        job_count=job_count,
        last_synced_at=repository.last_synced_at,
        source_type=repository.source_type,
        local_path=visible_local_path(request, repository),
        revision=repository.config_sha,
        github_connection_id=repository.github_connection_id,
        syncing=repository.sync_requested_at is not None,
    )


def validation_schema(
    repository: Repository, head: str | None, problem: str
) -> RepositoryValidationSchema:
    """`head` is None when the branch could not be read."""
    status = source_status(repository, head, problem)
    return RepositoryValidationSchema(
        status=status.status,
        message=status.message,
        current_revision=head,
        synced_revision=repository.config_sha,
        needs_sync=status.needs_sync,
    )


def unreachable(repository: Repository, error: Exception) -> RepositoryValidationSchema:
    return RepositoryValidationSchema(
        status=ValidationStatus.ERROR,
        message=str(error),
        current_revision=None,
        synced_revision=repository.config_sha,
        needs_sync=False,
    )


def validate_repository(repository: Repository) -> RepositoryValidationSchema:
    """Read the branch head, then report the state the last syncs saved.

    Only a sync reads the manifest and its linked skills. That can take
    seconds, so it runs in the worker. Pages and settings read what it saved.
    """
    try:
        if repository.source_type == Repository.SourceType.LOCAL:
            head = locate_local_repository(
                repository.local_path, repository.default_branch
            ).revision
        else:
            access = github_access(repository)
            head = repos.branch_head(
                access.token, access.repository_id, repository.default_branch
            )
    except (GitHubError, LocalRepositoryError) as error:
        return unreachable(repository, error)
    return validation_schema(repository, head, repository.last_sync_error)


def serialize_repository_detail(
    request: HttpRequest,
    repository: Repository,
) -> RepositoryDetailSchema:
    jobs = list(repository_jobs_with_grants(repository))
    summary = serialize_repository(request, repository, len(jobs))
    return RepositoryDetailSchema(
        **summary.model_dump(),
        config_path=repository.config_path,
        auto_sync=repository.auto_sync,
        server_syncs=server_syncs(),
        can_open_pull_requests=repository.is_github,
        allow_pull_requests=repository.allow_pull_requests,
        pull_request_ci=repository.pull_request_ci,
        allow_issues=repository.allow_issues,
        validation=validate_repository(repository),
        jobs=[
            RepositoryJobSchema(
                id=job.pk,
                name=job.name,
                skill_path=job.skill_path,
                cron=job.cron,
                timezone=job.timezone,
                enabled=job.enabled,
                grant_status=job.capability_grant_status(),
                removed_at=job.removed_at,
            )
            for job in jobs
        ],
    )
