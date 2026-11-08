"""Sync repositories whose branch moved, so merged changes apply.

Syncing only applies what is committed on the branch. New access a job
requests still waits for an owner's approval. A repository added before it
had a valid configuration is checked even while disabled, so the first
committed .secondshift.yaml shows up without a manual Resync.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.db.models import QuerySet

from secondshift.connections.models import Connection
from secondshift.github import repos
from secondshift.github.app import configured
from secondshift.github.errors import GitHubError
from secondshift.repositories.dao import repositories_to_sync, set_sync_error
from secondshift.repositories.github_access import can_check, github_access
from secondshift.repositories.github_check import check_after_failure
from secondshift.repositories.github_sync import sync_github_repository
from secondshift.repositories.local import (
    LocalRepositoryError,
    attach_local_repository,
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
            or token_sources().exists()
        )
        and settings.REPOSITORY_SYNC_INTERVAL_SECONDS > 0
    )


def token_sources() -> QuerySet[Connection]:
    return Connection.objects.filter(
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.API_KEY,
        status=Connection.Status.CONNECTED,
    )


def repositories_to_check() -> list[Repository]:
    return repositories_to_sync(settings.LOCAL_REPOSITORIES_ENABLED)


def sync_moved_repositories() -> int:
    """Sync each repository whose branch has a new commit. Returns how many."""
    synced = 0
    for repository in repositories_to_check():
        try:
            if repository.source_type == Repository.SourceType.GITHUB:
                if not can_check(repository.github_connection):
                    continue
                access = github_access(repository)
                head = repos.branch_head(
                    access.token, access.repository_id, repository.default_branch
                )
                if head == repository.checked_sha:
                    continue
                result = sync_github_repository(repository)
            else:
                head = branch_revision(
                    Path(repository.local_path), repository.default_branch
                )
                if head == repository.checked_sha:
                    continue
                result = attach_local_repository(
                    repository.workspace,
                    repository.local_path,
                    repository.default_branch,
                )
        except (LocalRepositoryError, GitHubError) as error:
            # The repository page shows the same problem when opened. Runs
            # keep using the last good sync until it is fixed.
            set_sync_error(repository.pk, str(error)[:500])
            logger.warning("could not sync %s: %s", repository.display_name, error)
            if repository.source_type == Repository.SourceType.GITHUB:
                check_after_failure(repository.github_connection)
            continue
        if result.last_sync_error:
            logger.warning(
                "could not sync %s: %s", repository.display_name, result.last_sync_error
            )
            continue
        synced += 1
    return synced
