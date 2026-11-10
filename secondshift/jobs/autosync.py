"""Sync repositories whose branch moved, so merged changes apply.

Syncing only applies what is committed on the branch. New access a job
requests still waits for an owner's approval. A repository added before it
had a valid configuration is checked even while disabled, so the first
committed .secondshift.yaml shows up without a manual Resync.
"""

import logging
from pathlib import Path

from django.conf import settings

from secondshift.connections.dao import has_github_token_sources
from secondshift.connections.github import can_check
from secondshift.github import repos
from secondshift.github.app import configured
from secondshift.github.errors import GitHubError
from secondshift.jobs.repository_sync import sync_repository
from secondshift.repositories.dao import repositories_to_sync, set_sync_error
from secondshift.repositories.github_access import github_access
from secondshift.repositories.github_check import check_after_failure
from secondshift.repositories.local import (
    LocalRepositoryError,
    branch_revision,
)
from secondshift.repositories.models import Repository

logger = logging.getLogger(__name__)


def server_syncs() -> bool:
    """Whether the worker syncs at all on this server."""
    return bool(
        (
            settings.LOCAL_REPOSITORIES_ENABLED
            or configured()
            or has_github_token_sources()
        )
        and settings.REPOSITORY_SYNC_INTERVAL_SECONDS > 0
    )


def branch_head(repository: Repository) -> str:
    if repository.source_type == Repository.SourceType.GITHUB:
        access = github_access(repository)
        return repos.branch_head(
            access.token, access.repository_id, repository.default_branch
        )
    return branch_revision(Path(repository.local_path), repository.default_branch)


def sync_moved_repositories() -> int:
    """Sync each repository whose branch has a new commit. Returns how many."""
    synced = 0
    for repository in repositories_to_sync(settings.LOCAL_REPOSITORIES_ENABLED):
        is_github = repository.source_type == Repository.SourceType.GITHUB
        if is_github and not can_check(repository.github_connection):
            continue
        try:
            if branch_head(repository) == repository.checked_sha:
                continue
            result = sync_repository(repository)
        except (LocalRepositoryError, GitHubError) as error:
            # The repository page shows the same problem when opened. Runs
            # keep using the last good sync until it is fixed.
            set_sync_error(repository.pk, str(error))
            logger.warning("could not sync %s: %s", repository.display_name, error)
            if is_github:
                check_after_failure(repository.github_connection)
            continue
        if result.last_sync_error:
            logger.warning(
                "could not sync %s: %s", repository.display_name, result.last_sync_error
            )
            continue
        synced += 1
    return synced
