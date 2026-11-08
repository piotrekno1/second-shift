import hashlib
import logging
import posixpath
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml
from django.db import transaction

from secondshift.config.constants import GITHUB_BASE_URL, MANIFEST_FILE
from secondshift.jobs.automatic_grants import approve_waiting
from secondshift.jobs.dao import reopen_github_mcp_grants, repository_active_requests
from secondshift.jobs.manifest import RepositoryManifest, parse_manifest
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.dao import local_repository, save_local_repository
from secondshift.repositories.github import github_remote_name
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace

logger = logging.getLogger(__name__)


class LocalRepositoryError(ValueError):
    pass


class RepositoryConfigError(LocalRepositoryError):
    """The branch's manifest, or a skill it names, is missing or invalid.

    The repository can still be added. Its page shows the problem.
    """


@dataclass(frozen=True)
class LocalRepositoryLocation:
    root: Path
    # The branch SecondShift follows, and its newest commit.
    branch: str
    revision: str
    repository_name: str
    clone_url: str = ""


@dataclass(frozen=True)
class LocalRepositoryMetadata:
    location: LocalRepositoryLocation
    manifest: RepositoryManifest
    # Each job's skill text at the revision, keyed by skill path.
    skills: dict[str, str]

    @property
    def revision(self) -> str:
        return self.location.revision

    @property
    def clone_url(self) -> str:
        return self.location.clone_url


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
        # Git's own output can name folders on this machine, so it stays in
        # the server log. Only owners may see local paths.
        logger.info(
            "git %s failed: %s",
            arguments[0] if arguments else "",
            completed.stderr.strip() or completed.stdout.strip(),
        )
        raise LocalRepositoryError("Git could not inspect the repository.")
    return completed.stdout.strip()


def committed_file(root: Path, revision: str, path: str) -> str:
    """A file's text at a commit. Only regular files inside the repository."""
    normalized = posixpath.normpath(path)
    if normalized.startswith(("../", "/")) or normalized in ("..", "."):
        raise LocalRepositoryError(f"{path} must be a file inside the repository.")
    try:
        entry = git(root, "ls-tree", revision, "--", normalized)
    except LocalRepositoryError as error:
        raise LocalRepositoryError(f"Git could not read {path}.") from error
    if not entry:
        raise LocalRepositoryError(f"{path} is not committed on the branch.")
    mode = entry.split(maxsplit=1)[0]
    if mode not in FILE_MODES:
        raise LocalRepositoryError(f"{path} must be a regular file, not a link.")
    try:
        return git(root, "show", f"{revision}:{normalized}")
    except LocalRepositoryError as error:
        raise LocalRepositoryError(f"Git could not read {path}.") from error


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
    try:
        skills = {
            job.skill: committed_file(root, revision, job.skill)
            for job in manifest.jobs.values()
        }
    except LocalRepositoryError as error:
        raise RepositoryConfigError(str(error)) from error
    return manifest, skills


def locate_local_repository(
    raw_path: str, branch: str | None = None
) -> LocalRepositoryLocation:
    """Find the repository and the newest commit on a branch.

    Without a branch it follows the one checked out now.
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
        github_name = github_remote_name(git(root, "remote", "get-url", "origin"))
    except LocalRepositoryError:
        github_name = None
    return LocalRepositoryLocation(
        root=root,
        branch=branch,
        revision=revision,
        repository_name=local_repository_name(root),
        clone_url=f"{GITHUB_BASE_URL}/{github_name}" if github_name else "",
    )


def inspect_local_repository(
    raw_path: str, branch: str | None = None
) -> LocalRepositoryMetadata:
    """Read the jobs from the newest commit on a branch.

    Only committed files count, so edits in the working folder never change
    what runs.
    """
    location = locate_local_repository(raw_path, branch)
    manifest, skills = read_committed_config(
        location.root, location.revision, location.branch
    )
    return LocalRepositoryMetadata(location=location, manifest=manifest, skills=skills)


def local_repository_name(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return f"local/{root.name}-{digest}"


@transaction.atomic
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
        manifest, skills, config_error = None, {}, str(error)[:500]
    previous = local_repository(workspace, str(location.root))
    scope_changed = previous is not None and previous.clone_url != location.clone_url
    synced = {"config_sha": location.revision} if manifest is not None else {}
    repository = save_local_repository(
        workspace,
        str(location.root),
        {
            "source_type": Repository.SourceType.LOCAL,
            "full_name": location.repository_name,
            "clone_url": location.clone_url,
            "default_branch": location.branch,
            "config_path": MANIFEST_FILE,
            "checked_sha": location.revision,
            "last_sync_error": config_error,
            "enabled": True,
            "removed_at": None,
            **synced,
        },
    )
    if manifest is not None:
        sync_jobs(repository, manifest, skills)
    if scope_changed:
        reopen_github_mcp_grants(repository)
        approve_waiting(repository_active_requests(repository))
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
