from django.http import HttpRequest

from secondshift.github import repos
from secondshift.github.errors import GitHubError
from secondshift.jobs.autosync import server_syncs
from secondshift.jobs.dao import repository_jobs_with_grants
from secondshift.jobs.repository_sync import (
    RepositoryConfigError,
    inspect_local_repository,
)
from secondshift.repositories.github_access import github_access
from secondshift.repositories.local import (
    LocalRepositoryError,
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
) -> RepositorySchema:
    return RepositorySchema(
        id=repository.pk,
        full_name=repository.full_name,
        display_name=repository.display_name,
        default_branch=repository.default_branch,
        enabled=repository.enabled,
        # The list endpoint prefetches jobs, so this reads them from memory.
        job_count=len(repository.jobs.all()),
        last_synced_at=repository.last_synced_at,
        source_type=repository.source_type,
        local_path=visible_local_path(request, repository),
        revision=repository.config_sha,
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
    """Read the branch head, then report the state the last syncs saved."""
    if repository.source_type != Repository.SourceType.LOCAL:
        try:
            access = github_access(repository)
            head = repos.branch_head(
                access.token, access.repository_id, repository.default_branch
            )
        except GitHubError as error:
            return unreachable(repository, error)
        return validation_schema(repository, head, repository.last_sync_error)
    try:
        metadata = inspect_local_repository(
            repository.local_path, repository.default_branch
        )
    except RepositoryConfigError as error:
        # The branch is readable, but its configuration is broken right now.
        return validation_schema(repository, None, str(error))
    except LocalRepositoryError as error:
        return unreachable(repository, error)
    return validation_schema(repository, metadata.revision, repository.last_sync_error)


def serialize_repository_detail(
    request: HttpRequest,
    repository: Repository,
) -> RepositoryDetailSchema:
    summary = serialize_repository(request, repository)
    jobs = repository_jobs_with_grants(repository)
    return RepositoryDetailSchema(
        **summary.model_dump(),
        config_path=repository.config_path,
        auto_sync=repository.auto_sync,
        server_syncs=server_syncs(),
        can_open_pull_requests=repository.source_type == Repository.SourceType.GITHUB,
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
