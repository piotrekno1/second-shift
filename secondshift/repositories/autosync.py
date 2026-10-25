"""Sync local repositories whose branch moved, so merged changes apply.

Syncing only applies what is committed on the branch. New access a job
requests still waits for an owner's approval.
"""

import logging
from pathlib import Path

from secondshift.repositories.local import (
    LocalRepositoryError,
    attach_local_repository,
    branch_revision,
)
from secondshift.repositories.models import Repository

logger = logging.getLogger(__name__)


def repositories_to_check() -> list[Repository]:
    return list(
        Repository.objects.filter(
            source_type=Repository.SourceType.LOCAL,
            enabled=True,
            removed_at__isnull=True,
        ).select_related("workspace")
    )


def sync_moved_repositories() -> int:
    """Sync each repository whose branch has a new commit. Returns how many."""
    synced = 0
    for repository in repositories_to_check():
        try:
            head = branch_revision(
                Path(repository.local_path), repository.default_branch
            )
            if head == repository.config_sha:
                continue
            attach_local_repository(
                repository.workspace, repository.local_path, repository.default_branch
            )
        except LocalRepositoryError as error:
            # The repository page shows the same problem when opened. Runs
            # keep using the last good sync until it is fixed.
            logger.warning("could not sync %s: %s", repository.display_name, error)
            continue
        synced += 1
    return synced
