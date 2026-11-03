"""Sync repositories whose branch moved, so merged changes apply.

Syncing only applies what is committed on the branch. New access a job
requests still waits for an owner's approval. A repository added before it
had a valid configuration is checked even while disabled, so the first
committed .secondshift.yaml shows up without a manual Resync.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.db.models import Q

from secondshift.connections.models import Connection
from secondshift.github.app import branch_head, configured
from secondshift.github.errors import GitHubError
from secondshift.repositories.github_sync import (
    github_repository_metadata,
    sync_github_repository,
)
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
        (settings.LOCAL_REPOSITORIES_ENABLED or configured())
        and settings.REPOSITORY_SYNC_INTERVAL_SECONDS > 0
    )


def repositories_to_check() -> list[Repository]:
    return list(
        Repository.objects.filter(
            Q(source_type=Repository.SourceType.GITHUB)
            | Q(source_type=Repository.SourceType.LOCAL, local_path__isnull=False)
            if settings.LOCAL_REPOSITORIES_ENABLED
            else Q(source_type=Repository.SourceType.GITHUB),
            Q(enabled=True) | Q(config_sha=""),
            auto_sync=True,
            removed_at__isnull=True,
        ).select_related("workspace", "github_connection")
    )


def sync_moved_repositories() -> int:
    """Sync each repository whose branch has a new commit. Returns how many."""
    synced = 0
    for repository in repositories_to_check():
        try:
            if repository.source_type == Repository.SourceType.GITHUB:
                connection = repository.github_connection
                if not connection or connection.status != Connection.Status.CONNECTED:
                    continue
                github_repository_metadata(repository)
                head = branch_head(
                    connection.external_account_id,
                    int(repository.external_id),
                    repository.default_branch,
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
            Repository.objects.filter(pk=repository.pk).update(
                last_sync_error=str(error)[:500]
            )
            logger.warning("could not sync %s: %s", repository.display_name, error)
            continue
        if result.last_sync_error:
            logger.warning(
                "could not sync %s: %s", repository.display_name, result.last_sync_error
            )
            continue
        synced += 1
    return synced
