"""Fetch GitHub source into the control plane's mirror. Tokens never leave it."""

from pathlib import Path

from django.db import transaction

from secondshift.connections.dao import connected_github_sources
from secondshift.connections.models import Connection
from secondshift.github import credentials, repos
from secondshift.github.app import configured, installation_repositories
from secondshift.github.errors import GitHubError
from secondshift.github.git import authenticated_git
from secondshift.github.identity import clone_url
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.dao import (
    get_repository_with_connection,
    lock_or_create_github_repository,
    lock_repository,
    restore_repository_fields,
    set_sync_error,
)
from secondshift.repositories.github_access import (
    GitHubAccess,
    connection_access,
    connection_source,
    github_access,
    usable_source,
)
from secondshift.repositories.local import (
    RepositoryConfigError,
    read_committed_config,
)
from secondshift.repositories.mirrors import SYNCED_REF, locked_mirror
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace


def sync_github_repository(repository: Repository) -> Repository:
    """Fetch the branch into the mirror and sync jobs from its newest commit.

    A missing or invalid configuration is saved in last_sync_error, and the
    jobs stay as the last good sync left them. The fetch runs outside any database transaction. The mirror's lock keeps
    two syncs of one repository in order, so an older one never saves last.
    """
    repository = get_repository_with_connection(repository.pk)
    access = github_access(repository)
    token = access.token
    with locked_mirror(repository.pk) as mirror:
        authenticated_git(
            mirror,
            token,
            "fetch",
            "--no-tags",
            "--",
            access.clone_url,
            f"refs/heads/{repository.default_branch}",
        )
        revision = authenticated_git(mirror, token, "rev-parse", "FETCH_HEAD")
        try:
            manifest, skills = read_committed_config(
                mirror, revision, repository.default_branch
            )
        except RepositoryConfigError as error:
            # Keep the last good sync. Runs go on using it.
            set_sync_error(repository.pk, str(error)[:500], checked_sha=revision)
            repository.refresh_from_db()
            return repository
        authenticated_git(mirror, token, "update-ref", SYNCED_REF, revision)
        with transaction.atomic():
            locked = lock_repository(repository.pk)
            locked.config_sha = revision
            locked.checked_sha = revision
            locked.last_sync_error = ""
            locked.save(
                update_fields=[
                    "config_sha",
                    "checked_sha",
                    "last_sync_error",
                    "updated_at",
                ]
            )
            sync_jobs(locked, manifest, skills)
    repository.refresh_from_db()
    return repository


def fetch_github_commit(access: GitHubAccess, commit_sha: str, mirror: Path) -> None:
    """Fetch one commit by its SHA into the repository's mirror."""
    try:
        authenticated_git(
            mirror,
            access.token,
            "fetch",
            "--no-tags",
            "--",
            access.clone_url,
            commit_sha,
        )
    except GitHubError as error:
        raise GitHubError(
            f"The commit {commit_sha[:12]} is no longer available from GitHub."
        ) from error


class GitHubRepositoryExistsError(GitHubError):
    pass


def source_repositories(connection: Connection) -> list[dict]:
    """What a source connection can import, as GitHub lists it."""
    source = connection_source(connection)
    if isinstance(source, credentials.AppInstallation):
        return installation_repositories(source.installation_id)
    return repos.token_repositories(source.token)


def available_github_sources(
    workspace: Workspace,
) -> tuple[list[dict[str, object]], list[str]]:
    available = []
    errors = []
    for connection in connected_github_sources(workspace.pk):
        if not usable_source(connection) or (
            connection.auth_strategy == Connection.AuthStrategy.MANAGED
            and not configured()
        ):
            continue
        try:
            repositories = source_repositories(connection)
        except GitHubError as error:
            errors.append(f"{connection.display_name}: {error}")
            continue
        for item in repositories:
            available.append(
                {
                    "connection_id": connection.pk,
                    "repository_id": item["id"],
                    "full_name": item["full_name"],
                    "default_branch": item["default_branch"],
                }
            )
    return available, errors


# A removed repository that is added again gets these back if its sync fails.
RESTORED_FIELDS = (
    "removed_at",
    "github_connection",
    "default_branch",
)


def attach_github_repository(
    workspace: Workspace, connection: Connection, repository_id: int, branch: str
) -> Repository:
    """Add a repository, or restore a removed one, then sync it.

    The row is saved first in a short transaction, so the GitHub fetch never
    holds a database lock. If the first sync fails, the row is put back.
    """
    metadata = connection_access(connection, repository_id).metadata
    with transaction.atomic():
        repository, previous = save_attached_repository(
            workspace, connection, repository_id, branch, metadata
        )
    try:
        return sync_github_repository(repository)
    except Exception:
        if previous is None:
            repository.delete()
        else:
            restore_repository_fields(repository.pk, previous)
        raise


def save_attached_repository(
    workspace: Workspace,
    connection: Connection,
    repository_id: int,
    branch: str,
    metadata: dict,
) -> tuple[Repository, dict | None]:
    """The new or restored row, and a restored row's earlier values."""
    repository, created = lock_or_create_github_repository(
        workspace,
        metadata["full_name"],
        defaults={
            "source_type": Repository.SourceType.GITHUB,
            "github_connection": connection,
            "external_id": str(repository_id),
            "clone_url": clone_url(metadata["full_name"]),
            "default_branch": branch or metadata["default_branch"],
        },
    )
    if created:
        return repository, None
    if (
        repository.removed_at is None
        or repository.source_type != Repository.SourceType.GITHUB
        or repository.external_id != str(repository_id)
    ):
        raise GitHubRepositoryExistsError("This repository is already connected.")
    previous = {field: getattr(repository, field) for field in RESTORED_FIELDS}
    repository.removed_at = None
    repository.github_connection = connection
    repository.default_branch = branch or metadata["default_branch"]
    repository.save()
    return repository, previous


def repository_branches(connection: Connection, repository_id: int) -> list[str]:
    access = connection_access(connection, repository_id)
    return repos.branches(access.token, access.full_name)
