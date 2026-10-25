import hashlib
import posixpath
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml
from django.db import transaction

from secondshift.automations.manifest import RepositoryManifest, parse_manifest
from secondshift.automations.models import JobGrant
from secondshift.automations.sync import sync_jobs
from secondshift.config.constants import GITHUB_BASE_URL, MANIFEST_FILE
from secondshift.integrations.models import Connection
from secondshift.repositories.github import github_remote_name
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace


class LocalRepositoryError(ValueError):
    pass


@dataclass(frozen=True)
class LocalRepositoryMetadata:
    root: Path
    # The branch SecondShift follows, and its newest commit.
    branch: str
    revision: str
    manifest: RepositoryManifest
    repository_name: str
    clone_url: str = ""


# Git file modes for a regular and an executable file. Symbolic links and
# submodules are refused so a path cannot point outside the repository.
FILE_MODES = {"100644", "100755"}


def git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise LocalRepositoryError("Git took too long to answer.") from error
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise LocalRepositoryError(message or "Git could not inspect the repository.")
    return completed.stdout.strip()


def committed_file(root: Path, revision: str, path: str) -> str:
    """A file's text at a commit. Only regular files inside the repository."""
    normalized = posixpath.normpath(path)
    if normalized.startswith(("../", "/")) or normalized in ("..", "."):
        raise LocalRepositoryError(f"{path} must be a file inside the repository.")
    entry = git(root, "ls-tree", revision, "--", normalized)
    if not entry:
        raise LocalRepositoryError(f"{path} is not committed on the branch.")
    mode = entry.split(maxsplit=1)[0]
    if mode not in FILE_MODES:
        raise LocalRepositoryError(f"{path} must be a regular file, not a link.")
    return git(root, "show", f"{revision}:{normalized}")


def current_branch(root: Path) -> str:
    try:
        return git(root, "symbolic-ref", "--short", "HEAD")
    except LocalRepositoryError as error:
        raise LocalRepositoryError(
            "Check out the branch SecondShift should follow, then add the repository."
        ) from error


def branch_revision(root: Path, branch: str) -> str:
    """The newest commit on a branch."""
    try:
        return git(root, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    except LocalRepositoryError as error:
        raise LocalRepositoryError(f"The branch {branch} does not exist.") from error


def inspect_local_repository(
    raw_path: str, branch: str | None = None
) -> LocalRepositoryMetadata:
    """Read the jobs from the newest commit on a branch.

    Without a branch it follows the one checked out now. Only committed files
    count, so edits in the working folder never change what runs.
    """
    candidate = Path(raw_path).expanduser().resolve()
    if not candidate.is_dir():
        raise LocalRepositoryError("The selected path is not a directory.")

    try:
        root = Path(git(candidate, "rev-parse", "--show-toplevel")).resolve()
    except LocalRepositoryError as error:
        raise LocalRepositoryError(
            "The selected path is not a Git repository."
        ) from error

    branch = branch or current_branch(root)
    revision = branch_revision(root, branch)

    try:
        text = committed_file(root, revision, MANIFEST_FILE)
    except LocalRepositoryError as error:
        raise LocalRepositoryError(
            f"The branch {branch} has no committed {MANIFEST_FILE} at its root."
        ) from error
    try:
        manifest = parse_manifest(text)
    except (ValueError, yaml.YAMLError) as error:
        raise LocalRepositoryError(f"Invalid {MANIFEST_FILE}: {error}") from error
    for job in manifest.jobs.values():
        committed_file(root, revision, job.skill)

    try:
        github_name = github_remote_name(git(root, "remote", "get-url", "origin"))
    except LocalRepositoryError:
        github_name = None
    return LocalRepositoryMetadata(
        root=root,
        branch=branch,
        revision=revision,
        manifest=manifest,
        repository_name=local_repository_name(root),
        clone_url=f"{GITHUB_BASE_URL}/{github_name}" if github_name else "",
    )


def local_repository_name(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return f"local/{root.name}-{digest}"


@transaction.atomic
def attach_local_repository(
    workspace: Workspace, raw_path: str, branch: str | None = None
) -> Repository:
    """Add a local repository, or sync one again from its branch."""
    metadata = inspect_local_repository(raw_path, branch)
    previous = Repository.objects.filter(
        workspace=workspace, local_path=str(metadata.root)
    ).first()
    scope_changed = previous is not None and previous.clone_url != metadata.clone_url
    repository, _ = Repository.objects.update_or_create(
        workspace=workspace,
        local_path=str(metadata.root),
        defaults={
            "source_type": Repository.SourceType.LOCAL,
            "full_name": metadata.repository_name,
            "clone_url": metadata.clone_url,
            "default_branch": metadata.branch,
            "config_path": MANIFEST_FILE,
            "config_sha": metadata.revision,
            "enabled": True,
            "removed_at": None,
        },
    )
    sync_jobs(repository, metadata.manifest)
    if scope_changed:
        JobGrant.objects.filter(
            capability_request__job__repository=repository,
            capability_request__kind=Connection.Kind.MCP,
            connection__provider=Connection.Provider.GITHUB,
            status=JobGrant.Status.APPROVED,
        ).update(status=JobGrant.Status.PENDING, reviewed_by=None, reviewed_at=None)
    return repository


def local_directories(raw_path: str) -> tuple[Path, Path | None, list[Path]]:
    requested = raw_path if raw_path.strip() else ""
    try:
        candidate = (
            Path(requested).expanduser().resolve()
            if requested
            else Path.home().resolve()
        )
    except (OSError, RuntimeError, ValueError) as error:
        target = "selected path" if requested else "home directory"
        raise LocalRepositoryError(f"Unable to open the {target}.") from error
    try:
        is_directory = candidate.is_dir()
    except (OSError, RuntimeError, ValueError) as error:
        raise LocalRepositoryError(f"Unable to access {candidate}.") from error
    if not is_directory:
        raise LocalRepositoryError(f"{candidate} is not a directory.")
    try:
        entries = list(candidate.iterdir())
        directories = sorted(
            (item for item in entries if item.is_dir()),
            key=lambda item: item.name.lower(),
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise LocalRepositoryError(f"Unable to read {candidate}.") from error
    parent = None if candidate.parent == candidate else candidate.parent
    return candidate, parent, directories
