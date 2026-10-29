from django.conf import settings
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.security import django_auth

from secondshift.connections.models import Connection
from secondshift.repositories.access import repository_for_request
from secondshift.repositories.github_app import (
    GitHubAppError,
    configured,
    repository_branches,
)
from secondshift.repositories.github_sync import (
    GitHubRepositoryExistsError,
    attach_github_repository,
    available_github_sources,
)
from secondshift.repositories.local import (
    LocalRepositoryError,
    attach_local_repository,
    local_directories,
)
from secondshift.repositories.models import Repository
from secondshift.repositories.schemas import (
    GitHubAppStatusSchema,
    GitHubAvailableRepositorySchema,
    GitHubRepositoryCreateSchema,
    LocalRepositoryCreateSchema,
    LocalRepositoryDirectoryListSchema,
    LocalRepositoryDirectorySchema,
    RepositoryDetailSchema,
    RepositoryEnabledSchema,
    RepositorySchema,
    RepositoryValidationSchema,
)
from secondshift.repositories.serialization import (
    serialize_repository,
    serialize_repository_detail,
    validate_repository,
)
from secondshift.repositories.sync import SyncError, resync
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.workspaces.access import (
    Action,
    current_workspace,
    require,
)

router = Router(auth=django_auth, tags=["repositories"])


@router.get("/repositories", response=list[RepositorySchema], auth=django_auth)
def repositories(request: HttpRequest) -> list[RepositorySchema]:
    workspace = current_workspace(request)
    if not workspace:
        return []
    queryset = Repository.objects.filter(
        workspace=workspace,
        removed_at__isnull=True,
    ).prefetch_related("jobs")
    return [serialize_repository(request, item) for item in queryset]


@router.post("/repositories/local", response=RepositorySchema, auth=django_auth)
def create_local_repository(
    request: HttpRequest,
    payload: LocalRepositoryCreateSchema,
) -> RepositorySchema:
    if not settings.LOCAL_REPOSITORIES_ENABLED:
        raise HttpError(404, "Local repositories are disabled.")
    workspace = require(request, Action.ADD_LOCAL_REPOSITORIES)
    try:
        repository = attach_local_repository(workspace, payload.path)
    except LocalRepositoryError as error:
        raise HttpError(422, str(error)) from error
    return serialize_repository(request, repository)


@router.get(
    "/repositories/local/directories",
    response=LocalRepositoryDirectoryListSchema,
    auth=django_auth,
)
def local_repository_directories(
    request: HttpRequest,
    path: str = Query(""),
) -> LocalRepositoryDirectoryListSchema:
    if not settings.LOCAL_REPOSITORIES_ENABLED:
        raise HttpError(404, "Local repositories are disabled.")
    require(request, Action.ADD_LOCAL_REPOSITORIES)
    try:
        candidate, parent, directories = local_directories(path)
    except LocalRepositoryError as error:
        raise HttpError(422, str(error)) from error
    return LocalRepositoryDirectoryListSchema(
        path=str(candidate),
        parent=str(parent) if parent else None,
        directories=[
            LocalRepositoryDirectorySchema(name=item.name, path=str(item))
            for item in directories
        ],
    )


@router.get("/repositories/github/available", response=GitHubAppStatusSchema)
def available_github_repositories(request: HttpRequest) -> GitHubAppStatusSchema:
    workspace = require(request, Action.ADD_GITHUB_REPOSITORIES)
    available, errors = (
        available_github_sources(workspace) if configured() else ([], [])
    )
    return GitHubAppStatusSchema(
        configured=configured(),
        error=" ".join(errors),
        repositories=[GitHubAvailableRepositorySchema(**item) for item in available],
    )


@router.get(
    "/repositories/github/{connection_id}/{repository_id}/branches", response=list[str]
)
def github_branches(
    request: HttpRequest, connection_id: int, repository_id: int
) -> list[str]:
    workspace = require(request, Action.ADD_GITHUB_REPOSITORIES)
    connection = get_object_or_404(
        Connection,
        pk=connection_id,
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.MANAGED,
        status=Connection.Status.CONNECTED,
    )
    try:
        return repository_branches(connection.external_account_id, repository_id)
    except GitHubAppError as error:
        raise HttpError(422, str(error)) from error


@router.post("/repositories/github", response=RepositorySchema)
def create_github_repository(
    request: HttpRequest, payload: GitHubRepositoryCreateSchema
) -> RepositorySchema:
    workspace = require(request, Action.ADD_GITHUB_REPOSITORIES)
    connection = get_object_or_404(
        Connection,
        pk=payload.connection_id,
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.MANAGED,
        status=Connection.Status.CONNECTED,
    )
    try:
        repository = attach_github_repository(
            workspace, connection, payload.repository_id, payload.branch
        )
    except GitHubRepositoryExistsError as error:
        raise HttpError(409, str(error)) from error
    except (GitHubAppError, RunnerProtocolError) as error:
        raise HttpError(422, str(error)) from error
    return serialize_repository(request, repository)


@router.get(
    "/repositories/{repository_id}",
    response=RepositoryDetailSchema,
    auth=django_auth,
)
def repository_detail(
    request: HttpRequest,
    repository_id: int,
) -> RepositoryDetailSchema:
    repository = repository_for_request(request, repository_id)
    return serialize_repository_detail(request, repository)


@router.post(
    "/repositories/{repository_id}/validate",
    response=RepositoryValidationSchema,
    auth=django_auth,
)
def validate_repository_endpoint(
    request: HttpRequest,
    repository_id: int,
) -> RepositoryValidationSchema:
    return validate_repository(repository_for_request(request, repository_id))


@router.post(
    "/repositories/{repository_id}/resync",
    response=RepositoryDetailSchema,
    auth=django_auth,
)
def resync_repository(
    request: HttpRequest,
    repository_id: int,
) -> RepositoryDetailSchema:
    repository = repository_for_request(request, repository_id)
    require(request, Action.RESYNC_REPOSITORIES)
    try:
        repository = resync(repository)
    except SyncError as error:
        raise HttpError(422, str(error)) from error
    return serialize_repository_detail(request, repository)


@router.post(
    "/repositories/{repository_id}/enabled",
    response=RepositoryDetailSchema,
    auth=django_auth,
)
def set_repository_enabled(
    request: HttpRequest,
    repository_id: int,
    payload: RepositoryEnabledSchema,
) -> RepositoryDetailSchema:
    repository = repository_for_request(request, repository_id)
    require(request, Action.CHANGE_REPOSITORY_STATE)
    if payload.enabled:
        validation = validate_repository(repository)
        if validation.status != "valid":
            raise HttpError(409, validation.message)
    repository.enabled = payload.enabled
    repository.save(update_fields=["enabled", "updated_at"])
    return serialize_repository_detail(request, repository)


@router.post(
    "/repositories/{repository_id}/auto-sync",
    response=RepositoryDetailSchema,
    auth=django_auth,
)
def set_repository_auto_sync(
    request: HttpRequest,
    repository_id: int,
    payload: RepositoryEnabledSchema,
) -> RepositoryDetailSchema:
    repository = repository_for_request(request, repository_id)
    require(request, Action.CHANGE_REPOSITORY_STATE)
    repository.auto_sync = payload.enabled
    repository.save(update_fields=["auto_sync", "updated_at"])
    return serialize_repository_detail(request, repository)


@router.delete(
    "/repositories/{repository_id}",
    response={204: None},
    auth=django_auth,
)
def remove_repository(request: HttpRequest, repository_id: int) -> Status[None]:
    repository = repository_for_request(request, repository_id)
    require(request, Action.REMOVE_REPOSITORIES)
    repository.enabled = False
    repository.removed_at = timezone.now()
    repository.save(update_fields=["enabled", "removed_at", "updated_at"])
    repository.jobs.update(enabled=False)
    return Status(204, None)
