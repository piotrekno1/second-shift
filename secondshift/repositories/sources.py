"""The snapshot of a commit's files that a run gets.

Snapshots are built on the control plane, cached by repository and commit, and
uploaded to the runner when a run starts. The cache can be deleted at any time.
A missing snapshot is built again from the mirror, or the local folder.
"""

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from secondshift.repositories.github_sync import fetch_github_commit
from secondshift.repositories.mirrors import has_commit, locked_mirror
from secondshift.repositories.models import Repository
from secondshift.repositories.snapshots import SnapshotError, write_snapshot

COMMIT_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


@dataclass(frozen=True)
class SourceSnapshot:
    path: Path
    sha256: str
    size_bytes: int


def snapshots_root() -> Path:
    return settings.SOURCE_CACHE_ROOT / "snapshots"


def snapshot_path(repository_id: int, commit_sha: str) -> Path:
    return snapshots_root() / str(repository_id) / f"{commit_sha}.tar.gz"


def source_snapshot(repository: Repository, commit_sha: str) -> SourceSnapshot:
    """The commit's snapshot, built and cached if needed."""
    if not COMMIT_SHA.fullmatch(commit_sha):
        raise SnapshotError("A full commit SHA is required.")
    path = snapshot_path(repository.pk, commit_sha)
    if path.exists():
        # Mark it recently used, so cache cleanup removes older ones first.
        path.touch()
    else:
        build_snapshot(repository, commit_sha, path)
    return SourceSnapshot(
        path=path, sha256=file_sha256(path), size_bytes=path.stat().st_size
    )


def build_snapshot(repository: Repository, commit_sha: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if repository.source_type == Repository.SourceType.LOCAL:
        if not settings.LOCAL_REPOSITORIES_ENABLED:
            raise SnapshotError(
                "Local repositories are available only on a single host."
            )
        write_atomically(Path(repository.local_path), commit_sha, path)
        return
    with locked_mirror(repository.pk) as mirror:
        if not has_commit(mirror, commit_sha):
            fetch_github_commit(repository, commit_sha, mirror)
        write_atomically(mirror, commit_sha, path)


def write_atomically(git_dir: Path, commit_sha: str, path: Path) -> None:
    # Two runs of one commit may build at once. Both write the same bytes, and
    # each replaces the file whole, so neither ever reads a partial snapshot.
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix="building-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            write_snapshot(git_dir, commit_sha, output)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
