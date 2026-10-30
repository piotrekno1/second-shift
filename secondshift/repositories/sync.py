"""Resync a repository on request, from its GitHub App or its local folder."""

from secondshift.repositories.github_app import GitHubAppError
from secondshift.repositories.github_sync import sync_github_repository
from secondshift.repositories.local import (
    LocalRepositoryError,
    attach_local_repository,
)
from secondshift.repositories.models import Repository


class SyncError(Exception):
    pass


def resync(repository: Repository) -> Repository:
    """Read the manifest and skills again from the branch's newest commit."""
    try:
        if repository.source_type == Repository.SourceType.GITHUB:
            return sync_github_repository(repository)
        return attach_local_repository(
            repository.workspace, repository.local_path, repository.default_branch
        )
    except (GitHubAppError, LocalRepositoryError) as error:
        raise SyncError(str(error)) from error
