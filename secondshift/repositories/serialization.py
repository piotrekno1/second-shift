from django.http import HttpRequest

from secondshift.repositories.autosync import server_syncs
from secondshift.repositories.local import (
    LocalRepositoryError,
    inspect_local_repository,
)
from secondshift.repositories.models import Repository
from secondshift.repositories.schemas import (
    RepositoryDetailSchema,
    RepositoryJobSchema,
    RepositorySchema,
    RepositoryValidationSchema,
)
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
        job_count=repository.jobs.count(),
        last_synced_at=repository.last_synced_at,
        source_type=repository.source_type,
        local_path=visible_local_path(request, repository),
        revision=repository.config_sha,
    )


def validate_repository(repository: Repository) -> RepositoryValidationSchema:
    if repository.source_type != Repository.SourceType.LOCAL:
        return RepositoryValidationSchema(
            status="valid",
            message="GitHub repository metadata is managed by its installation.",
            current_revision=repository.config_sha or None,
            synced_revision=repository.config_sha,
            needs_sync=False,
        )
    try:
        metadata = inspect_local_repository(
            repository.local_path, repository.default_branch
        )
    except LocalRepositoryError as error:
        return RepositoryValidationSchema(
            status="error",
            message=str(error),
            current_revision=None,
            synced_revision=repository.config_sha,
            needs_sync=False,
        )
    needs_sync = metadata.revision != repository.config_sha
    return RepositoryValidationSchema(
        status="warning" if needs_sync else "valid",
        message=(
            "The repository has a newer revision. Resync before enabling schedules."
            if needs_sync
            else "Manifest, skills, and synchronized revision are valid."
        ),
        current_revision=metadata.revision,
        synced_revision=repository.config_sha,
        needs_sync=needs_sync,
    )


def serialize_repository_detail(
    request: HttpRequest,
    repository: Repository,
) -> RepositoryDetailSchema:
    summary = serialize_repository(request, repository)
    jobs = repository.jobs.prefetch_related("capability_requests__grant").all()
    return RepositoryDetailSchema(
        **summary.model_dump(),
        config_path=repository.config_path,
        auto_sync=repository.auto_sync,
        server_syncs=server_syncs(),
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
            )
            for job in jobs
        ],
    )
