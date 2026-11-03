"""Fetch GitHub source into the control plane's mirror. Tokens never leave it."""

import re
from pathlib import Path

from django.db import transaction

from secondshift.config.constants import GITHUB_REPOSITORY_PATTERN
from secondshift.connections.models import Connection
from secondshift.github.app import (
    installation_repositories,
    installation_token,
    repository_metadata,
)
from secondshift.github.errors import GitHubError
from secondshift.github.git import authenticated_git
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.local import (
    RepositoryConfigError,
    read_committed_config,
)
from secondshift.repositories.mirrors import SYNCED_REF, locked_mirror
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace


def github_repository_metadata(repository: Repository) -> dict:
    if repository.removed_at is not None:
        raise GitHubError("This repository has been removed.")
    connection = repository.github_connection
    if (
        not connection
        or connection.workspace_id != repository.workspace_id
        or connection.kind != Connection.Kind.SOURCE
        or connection.auth_strategy != Connection.AuthStrategy.MANAGED
        or connection.provider != Connection.Provider.GITHUB
        or connection.status != Connection.Status.CONNECTED
    ):
        raise GitHubError("The GitHub installation is disconnected.")
    installation_id = connection.external_account_id
    metadata = repository_metadata(installation_id, int(repository.external_id))
    full_name = metadata["full_name"]
    if not re.fullmatch(GITHUB_REPOSITORY_PATTERN, full_name):
        raise GitHubError("Invalid GitHub repository name.")
    # Do not silently move a repository's identity or its existing approvals.
    if repository.full_name != full_name:
        raise GitHubError("Repository was renamed. Remove and import it again.")
    return metadata


def sync_github_repository(repository: Repository) -> Repository:
    """Fetch the branch into the mirror and sync jobs from its newest commit.

    A missing or invalid configuration is saved in last_sync_error, and the
    jobs stay as the last good sync left them. The fetch runs outside any database transaction. The mirror's lock keeps
    two syncs of one repository in order, so an older one never saves last.
    """
    repository = Repository.objects.select_related("github_connection").get(
        pk=repository.pk
    )
    metadata = github_repository_metadata(repository)
    token = installation_token(
        repository.github_connection.external_account_id, int(repository.external_id)
    )
    with locked_mirror(repository.pk) as mirror:
        authenticated_git(
            mirror,
            token,
            "fetch",
            "--no-tags",
            "--",
            f"https://github.com/{metadata['full_name']}.git",
            f"refs/heads/{repository.default_branch}",
        )
        revision = authenticated_git(mirror, token, "rev-parse", "FETCH_HEAD")
        try:
            manifest, skills = read_committed_config(
                mirror, revision, repository.default_branch
            )
        except RepositoryConfigError as error:
            # Keep the last good sync. Runs go on using it.
            Repository.objects.filter(pk=repository.pk).update(
                checked_sha=revision, last_sync_error=str(error)[:500]
            )
            repository.refresh_from_db()
            return repository
        authenticated_git(mirror, token, "update-ref", SYNCED_REF, revision)
        with transaction.atomic():
            locked = Repository.objects.select_for_update().get(pk=repository.pk)
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


def fetch_github_commit(repository: Repository, commit_sha: str, mirror: Path) -> None:
    """Fetch one commit by its SHA into the repository's mirror."""
    metadata = github_repository_metadata(repository)
    token = installation_token(
        repository.github_connection.external_account_id, int(repository.external_id)
    )
    try:
        authenticated_git(
            mirror,
            token,
            "fetch",
            "--no-tags",
            "--",
            f"https://github.com/{metadata['full_name']}.git",
            commit_sha,
        )
    except GitHubError as error:
        raise GitHubError(
            f"The commit {commit_sha[:12]} is no longer available from GitHub."
        ) from error


class GitHubRepositoryExistsError(GitHubError):
    pass


def available_github_sources(
    workspace: Workspace,
) -> tuple[list[dict[str, object]], list[str]]:
    available = []
    errors = []
    connections = Connection.objects.filter(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.MANAGED,
        status=Connection.Status.CONNECTED,
    )
    for connection in connections:
        try:
            repositories = installation_repositories(connection.external_account_id)
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
    "installation_id",
    "default_branch",
)


def attach_github_repository(
    workspace: Workspace, connection: Connection, repository_id: int, branch: str
) -> Repository:
    """Add a repository, or restore a removed one, then sync it.

    The row is saved first in a short transaction, so the GitHub fetch never
    holds a database lock. If the first sync fails, the row is put back.
    """
    metadata = repository_metadata(connection.external_account_id, repository_id)
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
            Repository.objects.filter(pk=repository.pk).update(**previous)
        raise


def save_attached_repository(
    workspace: Workspace,
    connection: Connection,
    repository_id: int,
    branch: str,
    metadata: dict,
) -> tuple[Repository, dict | None]:
    """The new or restored row, and a restored row's earlier values."""
    repository, created = Repository.objects.select_for_update().get_or_create(
        workspace=workspace,
        full_name=metadata["full_name"],
        defaults={
            "source_type": Repository.SourceType.GITHUB,
            "github_connection": connection,
            "external_id": str(repository_id),
            "installation_id": connection.external_account_id,
            "clone_url": f"https://github.com/{metadata['full_name']}.git",
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
    repository.installation_id = connection.external_account_id
    repository.default_branch = branch or metadata["default_branch"]
    repository.save()
    return repository, previous
