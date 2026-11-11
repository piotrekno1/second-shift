"""Bring a repository's jobs up to date with its committed configuration.

The repositories app reads the source. This module reads the manifest and
skills from it and syncs the jobs, so source code never knows about jobs.
Git runs outside database transactions.
"""

from pathlib import Path

import yaml
from django.db import transaction

from secondshift.config.constants import MANIFEST_FILE
from secondshift.connections.models import Connection
from secondshift.github.errors import GitHubError
from secondshift.jobs.automatic_grants import approve_waiting
from secondshift.jobs.dao import reopen_github_mcp_grants, repository_active_requests
from secondshift.jobs.manifest import RepositoryManifest, parse_manifest, skill_key
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.dao import (
    get_repository_with_connection,
    restore_repository_fields,
    set_sync_error,
)
from secondshift.repositories.external_skills import (
    ExternalSkillError,
    resolve_external_skill,
)
from secondshift.repositories.github_access import connection_access
from secondshift.repositories.github_sync import (
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
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace


class RepositoryConfigError(LocalRepositoryError):
    """The branch's manifest, or a skill it names, is missing or invalid.

    The repository can still be added. Its page shows the problem.
    """


class SyncError(Exception):
    pass


def read_committed_config(
    root: Path, revision: str, branch: str
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
        for job in manifest.jobs.values():
            if source := job.skill_source:
                skills[skill_key(job)] = resolve_external_skill(
                    source.repository, job.skill, source.ref
                ).text
            else:
                skills[skill_key(job)] = committed_file(root, revision, job.skill)
    except ExternalSkillError as error:
        raise RepositoryConfigError(f"Invalid {MANIFEST_FILE}: {error}") from error
    except LocalRepositoryError as error:
        raise RepositoryConfigError(str(error)) from error
    return manifest, skills


def inspect_local_repository(
    raw_path: str, branch: str | None = None
) -> LocalRepositoryLocation:
    """Check the newest commit on a branch has a valid configuration.

    Only committed files count, so edits in the working folder never change
    what runs.
    """
    location = locate_local_repository(raw_path, branch)
    read_committed_config(location.root, location.revision, location.branch)
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
            location.root, location.revision, location.branch
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
                branch.mirror, branch.revision, repository.default_branch
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


def sync_repository(repository: Repository) -> Repository:
    """Sync from the branch's newest commit, from GitHub or the local folder."""
    if repository.source_type == Repository.SourceType.GITHUB:
        return sync_github_repository(repository)
    return attach_local_repository(
        repository.workspace, repository.local_path, repository.default_branch
    )


def resync(repository: Repository) -> Repository:
    """Read the manifest and skills again from the branch's newest commit.

    A configuration problem is saved on the repository and raised, so the
    person who pressed Resync sees it.
    """
    try:
        synced = sync_repository(repository)
    except (GitHubError, LocalRepositoryError) as error:
        raise SyncError(str(error)) from error
    if synced.last_sync_error:
        raise SyncError(synced.last_sync_error)
    return synced
