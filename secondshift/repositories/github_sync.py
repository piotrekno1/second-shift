"""Fetch GitHub source in trusted infrastructure, and publish a pinned snapshot."""

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml
from django.db import transaction

from secondshift.config.constants import (
    GITHUB_REPOSITORY_PATTERN,
    MANIFEST_FILE,
    MAX_REPOSITORY_BUNDLE_BYTES,
)
from secondshift.connections.models import Connection
from secondshift.jobs.manifest import parse_manifest
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.github_app import (
    GitHubAppError,
    installation_repositories,
    installation_token,
    repository_metadata,
)
from secondshift.repositories.local import LocalRepositoryError, committed_file
from secondshift.repositories.models import Repository
from secondshift.runner.client import get_runner_client
from secondshift.workspaces.models import Workspace


def github_repository_metadata(repository: Repository) -> dict:
    if repository.removed_at is not None:
        raise GitHubAppError("This repository has been removed.")
    connection = repository.github_connection
    if (
        not connection
        or connection.workspace_id != repository.workspace_id
        or connection.kind != Connection.Kind.SOURCE
        or connection.auth_strategy != Connection.AuthStrategy.MANAGED
        or connection.provider != Connection.Provider.GITHUB
        or connection.status != Connection.Status.CONNECTED
    ):
        raise GitHubAppError("The GitHub installation is disconnected.")
    installation_id = connection.external_account_id
    metadata = repository_metadata(installation_id, int(repository.external_id))
    full_name = metadata["full_name"]
    if not re.fullmatch(GITHUB_REPOSITORY_PATTERN, full_name):
        raise GitHubAppError("Invalid GitHub repository name.")
    # Do not silently move a repository's identity or its existing approvals.
    if repository.full_name != full_name:
        raise GitHubAppError("Repository was renamed. Remove and import it again.")
    return metadata


@transaction.atomic
def sync_github_repository(repository: Repository) -> Repository:
    repository = (
        Repository.objects.select_for_update(of=("self",))
        .select_related("github_connection")
        .get(pk=repository.pk)
    )
    metadata = github_repository_metadata(repository)
    connection = repository.github_connection
    installation_id = connection.external_account_id
    full_name = metadata["full_name"]
    token = installation_token(installation_id, int(repository.external_id))
    with tempfile.TemporaryDirectory(prefix="secondshift-github-") as temporary:
        root = Path(temporary)
        source = root / "source.git"
        source.mkdir()
        git_fetch(source, token, "init", "--bare")
        git_fetch(
            source,
            token,
            "fetch",
            "--no-tags",
            "--",
            f"https://github.com/{full_name}.git",
            f"refs/heads/{repository.default_branch}",
        )
        revision = git_fetch(source, token, "rev-parse", "FETCH_HEAD")
        try:
            manifest = parse_manifest(committed_file(source, revision, MANIFEST_FILE))
            skills = {
                job.skill: committed_file(source, revision, job.skill)
                for job in manifest.jobs.values()
            }
        except (LocalRepositoryError, ValueError, yaml.YAMLError) as error:
            raise GitHubAppError(
                f"Invalid committed repository configuration: {error}"
            ) from error
        git_fetch(source, token, "update-ref", "refs/heads/snapshot", revision)
        bundle = root / "source.bundle"
        git_fetch(source, token, "bundle", "create", str(bundle), "refs/heads/snapshot")
        if bundle.stat().st_size > MAX_REPOSITORY_BUNDLE_BYTES:
            raise GitHubAppError("Repository exceeds the 64 MiB bundle limit.")
        get_runner_client().import_repository(full_name, revision, bundle.read_bytes())
    with transaction.atomic():
        locked = Repository.objects.select_for_update().get(pk=repository.pk)
        locked.config_sha = revision
        locked.last_sync_error = ""
        locked.save(update_fields=["config_sha", "last_sync_error", "updated_at"])
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
        git_fetch(
            mirror,
            token,
            "fetch",
            "--no-tags",
            "--",
            f"https://github.com/{metadata['full_name']}.git",
            commit_sha,
        )
    except GitHubAppError as error:
        raise GitHubAppError(
            f"The commit {commit_sha[:12]} is no longer available from GitHub."
        ) from error


def git_fetch(root: Path, token: str, *arguments: str) -> str:
    # Never put credentials in URLs, arguments, persisted config, or errors.
    authorization = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {authorization}",
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GitHubAppError("GitHub repository fetch timed out.") from error
    if result.returncode:
        raise GitHubAppError("Could not fetch the GitHub repository and branch.")
    return result.stdout.strip()


class GitHubRepositoryExistsError(GitHubAppError):
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
        except GitHubAppError as error:
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


@transaction.atomic
def attach_github_repository(
    workspace: Workspace, connection: Connection, repository_id: int, branch: str
) -> Repository:
    metadata = repository_metadata(connection.external_account_id, repository_id)
    repository, created = Repository.objects.get_or_create(
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
    if not created:
        if (
            repository.removed_at is None
            or repository.source_type != Repository.SourceType.GITHUB
            or repository.external_id != str(repository_id)
        ):
            raise GitHubRepositoryExistsError("This repository is already connected.")
        repository.removed_at = None
        repository.github_connection = connection
        repository.installation_id = connection.external_account_id
        repository.default_branch = branch or metadata["default_branch"]
        repository.save()
    return sync_github_repository(repository)
