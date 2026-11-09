import hashlib
import logging
import posixpath
import subprocess
from dataclasses import dataclass
from pathlib import Path

from secondshift.config.constants import GITHUB_BASE_URL, MANIFEST_FILE
from secondshift.repositories.dao import local_repository, save_local_repository
from secondshift.repositories.github import github_remote_name
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace

logger = logging.getLogger(__name__)


class LocalRepositoryError(ValueError):
    pass


@dataclass(frozen=True)
class LocalRepositoryLocation:
    root: Path
    # The branch SecondShift follows, and its newest commit.
    branch: str
    revision: str
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


def local_repository_name(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return f"local/{root.name}-{digest}"


def save_local_location(
    workspace: Workspace,
    location: LocalRepositoryLocation,
    config_error: str,
    config_read: bool,
) -> tuple[Repository, bool]:
    """Save what was found in the folder.

    Returns the repository and whether its GitHub origin changed.
    """
    previous = local_repository(workspace, str(location.root))
    origin_changed = previous is not None and previous.clone_url != location.clone_url
    synced = {"config_sha": location.revision} if config_read else {}
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
    return repository, origin_changed


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
