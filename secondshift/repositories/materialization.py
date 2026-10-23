import fcntl
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class RepositoryMaterializationError(RuntimeError):
    pass


def materialize_local_repository(
    *,
    source: Path,
    repository_name: str,
    commit_sha: str,
    repository_root: Path,
) -> Path:
    """Copy a local Git repository into the development runner's owned store.

    This is a development bridge, not the hosted repository-ingestion path. The
    mirror contains Git objects but no working tree and is updated under a
    per-repository file lock so concurrent scheduled runs are safe.
    """
    source = source.resolve()
    repository_root = repository_root.resolve()
    if not source.is_dir():
        raise RepositoryMaterializationError("The local repository is unavailable.")
    repository_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = repository_root / repository_name
    target = candidate.resolve()
    if target == repository_root or not target.is_relative_to(repository_root):
        raise RepositoryMaterializationError(
            "The repository name escaped the runner repository root."
        )
    if candidate.is_symlink():
        raise RepositoryMaterializationError(
            "The runner repository target cannot be a symbolic link."
        )
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    locks = repository_root / ".locks"
    locks.mkdir(mode=0o700, exist_ok=True)
    lock_path = locks / (repository_name.replace("/", "__") + ".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        update_mirror(source, target)
        git_in(target, "cat-file", "-e", f"{commit_sha}^{{commit}}")
    return target


def update_mirror(source: Path, target: Path) -> None:
    if target.exists():
        try:
            is_bare = git_in(target, "rev-parse", "--is-bare-repository") == "true"
        except RepositoryMaterializationError as error:
            raise RepositoryMaterializationError(
                "The runner repository target is not an owned bare Git mirror."
            ) from error
        if target.is_symlink() or not is_bare:
            raise RepositoryMaterializationError(
                "The runner repository target is not an owned bare Git mirror."
            )
        git_in(
            target,
            "fetch",
            "--force",
            "--prune",
            str(source),
            "+refs/*:refs/*",
        )
        return

    temporary_root = Path(tempfile.mkdtemp(prefix="materialize-", dir=target.parent))
    temporary_mirror = temporary_root / "repository.git"
    try:
        run_git(
            "clone",
            "--mirror",
            "--no-local",
            str(source),
            str(temporary_mirror),
        )
        git_in(temporary_mirror, "config", "--unset-all", "remote.origin.url")
        os.replace(temporary_mirror, target)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def git_in(repository: Path, *arguments: str) -> str:
    return run_git("-C", str(repository), *arguments)


def run_git(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise RepositoryMaterializationError(
            "Git timed out while preparing the runner repository."
        ) from error
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RepositoryMaterializationError(
            detail or "Git could not prepare the runner repository."
        )
    return completed.stdout.strip()
