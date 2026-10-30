"""The control plane's Git mirror of each GitHub repository.

A mirror is a cache. It is keyed by repository ID, so two workspaces never
share one, and deleting it only costs a full fetch the next time.
"""

import fcntl
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

# The commit the repository last synced. It keeps that commit's objects in the
# mirror. Older commits are kept only until Git's own cleanup removes them.
SYNCED_REF = "refs/secondshift/synced"


def mirror_path(repository_id: int) -> Path:
    return settings.SOURCE_CACHE_ROOT / "mirrors" / f"{repository_id}.git"


@contextmanager
def locked_mirror(repository_id: int) -> Iterator[Path]:
    """The repository's bare mirror, created if missing, held under a lock."""
    path = mirror_path(repository_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    locks = settings.SOURCE_CACHE_ROOT / "locks"
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (locks / f"{repository_id}.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            subprocess.run(
                ["git", "init", "--quiet", "--bare", str(path)],
                check=True,
                capture_output=True,
                timeout=30,
            )
        yield path


def has_commit(mirror: Path, commit_sha: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(mirror), "cat-file", "-e", f"{commit_sha}^{{commit}}"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    return completed.returncode == 0
