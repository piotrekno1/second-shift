"""Resync a repository on request, from its GitHub App or its local folder."""

from secondshift.github.errors import GitHubError
from secondshift.repositories.github_sync import sync_github_repository
from secondshift.repositories.local import (
    LocalRepositoryError,
    attach_local_repository,
)
from secondshift.repositories.models import Repository


class SyncError(Exception):
    pass


def resync(repository: Repository) -> Repository:
    """Read the manifest and skills again from the branch's newest commit.

    A configuration problem is saved on the repository and raised, so the
    person who pressed Resync sees it.
    """
    try:
        if repository.source_type == Repository.SourceType.GITHUB:
            synced = sync_github_repository(repository)
        else:
            synced = attach_local_repository(
                repository.workspace, repository.local_path, repository.default_branch
            )
    except (GitHubError, LocalRepositoryError) as error:
        raise SyncError(str(error)) from error
    if synced.last_sync_error:
        raise SyncError(synced.last_sync_error)
    return synced
