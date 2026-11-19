"""Bring a repository's jobs up to date with its committed configuration.

The repositories app reads the source. This module reads the manifest and
skills from it and syncs the jobs, so source code never knows about jobs.
Git runs outside database transactions. Syncs run only in the worker, one
at a time. Resync, adding a repository and new commits all ask for one with
request_sync, and the page shows it as syncing until it finishes.
"""

import logging
import time
from pathlib import Path

import yaml
from django.db import transaction

from secondshift.connections.models import Connection
from secondshift.github.errors import GitHubError
from secondshift.jobs.automatic_grants import approve_waiting
from secondshift.jobs.dao import reopen_github_mcp_grants, repository_active_requests
from secondshift.jobs.manifest import RepositoryManifest, parse_manifest, skill_key
from secondshift.jobs.manifest_sync import sync_jobs
from secondshift.repositories.dao import (
    finish_sync_request,
    get_repository_with_connection,
    request_sync,
    requested_syncs,
    set_sync_error,
    sync_pending,
)
from secondshift.repositories.external_skills import (
    ExternalSkillError,
    resolve_external_skill,
    skill_readers,
)
from secondshift.repositories.github.access import connection_access
from secondshift.repositories.github.check import check_after_failure
from secondshift.repositories.github.sync import (
    fetched_branch,
    record_synced_revision,
    save_attached_repository,
)
from secondshift.repositories.local import (
    LocalRepositoryError,
    LocalRepositoryLocation,
    committed_file,
    locate_local_repository,
    save_local_location,
)
from secondshift.repositories.models import MANIFEST_FILE, Repository
from secondshift.workspaces.models import Workspace

logger = logging.getLogger(__name__)


class RepositoryConfigError(LocalRepositoryError):
    """The branch's manifest, or a skill it names, is missing or invalid.

    The repository can still be added. Its page shows the problem.
    """


UNEXPECTED_SYNC_ERROR = "The sync failed unexpectedly. The worker log has details."


def read_committed_config(
    workspace_id: int, root: Path, revision: str, branch: str
) -> tuple[RepositoryManifest, dict[str, str]]:
    """The manifest and its skills at a commit. Raises RepositoryConfigError."""
    try:
        text = committed_file(root, revision, MANIFEST_FILE)
    except LocalRepositoryError as error:
        raise RepositoryConfigError(
            f"The branch {branch} has no committed {MANIFEST_FILE} at its root."
        ) from error
    try:
        manifest = parse_manifest(text)
    except (ValueError, yaml.YAMLError) as error:
        raise RepositoryConfigError(f"Invalid {MANIFEST_FILE}: {error}") from error
    skills = {}
    try:
        linked = [
            job.skill_source.repository
            for job in manifest.jobs.values()
            if job.skill_source
        ]
        readers = skill_readers(workspace_id, linked) if linked else {}
        for job in manifest.jobs.values():
            if source := job.skill_source:
                skills[skill_key(job)] = resolve_external_skill(
                    workspace_id,
                    source.repository,
                    job.skill,
                    source.ref,
                    reader=readers[source.repository],
                ).text
            else:
                skills[skill_key(job)] = committed_file(root, revision, job.skill)
    except ExternalSkillError as error:
        raise RepositoryConfigError(f"Invalid {MANIFEST_FILE}: {error}") from error
    except LocalRepositoryError as error:
        raise RepositoryConfigError(str(error)) from error
    return manifest, skills


def inspect_local_repository(
    workspace_id: int, raw_path: str, branch: str | None = None
) -> LocalRepositoryLocation:
    """Check the newest commit on a branch has a valid configuration.

    Only committed files count, so edits in the working folder never change
    what runs.
    """
    location = locate_local_repository(raw_path, branch)
    read_committed_config(
        workspace_id, location.root, location.revision, location.branch
    )
    return location


def attach_local_repository(
    workspace: Workspace, raw_path: str, branch: str | None = None
) -> Repository:
    """Add a local repository, or sync one again from its branch.

    A missing or invalid configuration does not stop it. The repository is
    saved with the problem in last_sync_error, and its jobs stay as the last
    good sync left them.
    """
    location = locate_local_repository(raw_path, branch)
    try:
        manifest, skills = read_committed_config(
            workspace.pk, location.root, location.revision, location.branch
        )
        config_error = ""
    except RepositoryConfigError as error:
        manifest, skills, config_error = None, {}, str(error)
    with transaction.atomic():
        repository, origin_changed = save_local_location(
            workspace, location, config_error, manifest is not None
        )
        if manifest is not None:
            sync_jobs(repository, manifest, skills)
        if origin_changed:
            reopen_github_mcp_grants(repository)
            approve_waiting(repository_active_requests(repository))
    return repository


def sync_github_repository(repository: Repository) -> Repository:
    """Fetch the branch into the mirror and sync jobs from its newest commit.

    A missing or invalid configuration is saved in last_sync_error, and the
    jobs stay as the last good sync left them. The mirror's lock keeps two
    syncs of one repository in order, so an older one never saves last.
    """
    repository = get_repository_with_connection(repository.pk)
    with fetched_branch(repository) as branch:
        try:
            manifest, skills = read_committed_config(
                repository.workspace_id,
                branch.mirror,
                branch.revision,
                repository.default_branch,
            )
        except RepositoryConfigError as error:
            # Keep the last good sync. Runs go on using it.
            set_sync_error(repository.pk, str(error), checked_sha=branch.revision)
            repository.refresh_from_db()
            return repository
        branch.mark_synced()
        with transaction.atomic():
            locked = record_synced_revision(repository.pk, branch.revision)
            sync_jobs(locked, manifest, skills)
    repository.refresh_from_db()
    return repository


def add_github_repository(
    workspace: Workspace, connection: Connection, repository_id: int, branch: str
) -> Repository:
    """Add a repository, or restore a removed one. The worker syncs it."""
    metadata = connection_access(connection, repository_id).metadata
    with transaction.atomic():
        repository = save_attached_repository(
            workspace, connection, repository_id, branch, metadata
        )
    request_sync(repository.pk)
    repository.refresh_from_db()
    return repository


def add_local_repository(workspace: Workspace, raw_path: str) -> Repository:
    """Add a local repository, or restore a removed one. The worker syncs it.

    Raises LocalRepositoryError when the folder is not a Git repository.
    """
    location = locate_local_repository(raw_path)
    repository, _ = save_local_location(
        workspace, location, config_error="", config_read=False
    )
    request_sync(repository.pk)
    repository.refresh_from_db()
    return repository


def sync_repository(repository: Repository) -> Repository:
    """Sync from the branch's newest commit, from GitHub or the local folder.

    Only the worker calls this, through run_requested_syncs. Everything else
    asks for a sync with request_sync.
    """
    if repository.source_type == Repository.SourceType.GITHUB:
        return sync_github_repository(repository)
    return attach_local_repository(
        repository.workspace, repository.local_path, repository.default_branch
    )


def run_requested_syncs() -> int:
    """Run each sync that was asked for, one at a time. Returns how many.

    A failure is saved on the repository, as a broken manifest is, and the
    worker moves on to the next one.
    """
    synced = 0
    for repository in requested_syncs():
        requested_at = repository.sync_requested_at
        assert requested_at is not None
        try:
            result = sync_repository(repository)
        except (GitHubError, LocalRepositoryError) as error:
            set_sync_error(repository.pk, str(error))
            logger.warning("could not sync %s: %s", repository.display_name, error)
            if repository.source_type == Repository.SourceType.GITHUB:
                check_after_failure(repository.github_connection)
        except Exception:
            set_sync_error(repository.pk, UNEXPECTED_SYNC_ERROR)
            logger.exception("could not sync %s", repository.display_name)
        else:
            if result.last_sync_error:
                logger.warning(
                    "could not sync %s: %s",
                    repository.display_name,
                    result.last_sync_error,
                )
            else:
                synced += 1
        finally:
            finish_sync_request(repository.pk, requested_at)
    return synced


def wait_for_sync(repository_id: int, timeout: float, poll: float = 0.5) -> bool:
    """Wait for the worker to finish a requested sync. False on timeout."""
    deadline = time.monotonic() + timeout
    while sync_pending(repository_id):
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
    return True
