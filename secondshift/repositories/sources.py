"""The snapshot of a commit's files that a run gets.

Snapshots are built on the control plane, cached by repository and commit, and
uploaded to the runner when a run starts. The cache can be deleted at any time.
A missing snapshot is built again from the mirror, or the local folder. The
cache is kept under SOURCE_CACHE_MAX_BYTES, least recently used first.
"""

import hashlib
import os
import re
import shutil
import tempfile
import time
from collections.abc import Collection
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
        trim_snapshots(keep=path)
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


@dataclass
class CachePrune:
    snapshots: int = 0
    mirrors: int = 0
    bytes: int = 0

    def add(self, path: Path) -> None:
        self.bytes += tree_size(path)
        if path.name.endswith(".git"):
            self.mirrors += 1
        else:
            self.snapshots += 1


def prune_source_cache(dry_run: bool = False) -> CachePrune:
    """Remove cached sources no repository needs, then trim snapshots to size.

    Only entries named by a repository ID are touched, and only inside the
    cache folder. Removed repositories lose their mirror and snapshots.
    """
    pruned = CachePrune()
    active = set(
        Repository.objects.filter(removed_at__isnull=True).values_list("pk", flat=True)
    )
    removed = set()
    for folder in cache_entries(snapshots_root(), suffix=""):
        if int(folder.name) not in active:
            removed.add(int(folder.name))
            for snapshot in folder.glob("*.tar.gz"):
                pruned.add(snapshot)
            if not dry_run:
                shutil.rmtree(folder)
    for mirror in cache_entries(settings.SOURCE_CACHE_ROOT / "mirrors", ".git"):
        repository_id = int(mirror.name.removesuffix(".git"))
        if repository_id not in active:
            pruned.add(mirror)
            if not dry_run:
                with locked_mirror(repository_id) as path:
                    shutil.rmtree(path)
    remove_stale_builds(pruned, dry_run)
    trim_snapshots(pruned, dry_run=dry_run, skip=removed)
    return pruned


def trim_snapshots(
    pruned: CachePrune | None = None,
    dry_run: bool = False,
    keep: Path | None = None,
    skip: Collection[int] = (),
) -> CachePrune:
    """Remove the least recently used snapshots past SOURCE_CACHE_MAX_BYTES."""
    pruned = pruned or CachePrune()
    files = sorted(
        (
            path
            for folder in cache_entries(snapshots_root(), suffix="")
            if int(folder.name) not in skip
            for path in folder.glob("*.tar.gz")
            if path.is_file() and not path.is_symlink()
        ),
        key=lambda path: path.stat().st_mtime,
    )
    total = sum(path.stat().st_size for path in files)
    for path in files:
        if total <= settings.SOURCE_CACHE_MAX_BYTES:
            break
        if path == keep:
            continue
        size = path.stat().st_size
        total -= size
        pruned.snapshots += 1
        pruned.bytes += size
        if not dry_run:
            path.unlink(missing_ok=True)
    return pruned


def remove_stale_builds(pruned: CachePrune, dry_run: bool) -> None:
    """Remove partial snapshots left by a build that crashed."""
    for folder in cache_entries(snapshots_root(), suffix=""):
        for path in folder.glob("building-*"):
            if path.is_file() and not path.is_symlink() and is_stale(path):
                pruned.bytes += path.stat().st_size
                if not dry_run:
                    path.unlink(missing_ok=True)


def cache_entries(root: Path, suffix: str) -> list[Path]:
    """Folders in the cache named `<repository id><suffix>`, never links."""
    if not root.is_dir():
        return []
    return [
        path
        for path in root.iterdir()
        if path.name.removesuffix(suffix).isdigit()
        and path.name.endswith(suffix)
        and path.is_dir()
        and not path.is_symlink()
    ]


def is_stale(path: Path) -> bool:
    return path.stat().st_mtime < time.time() - 60 * 60


def tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(
        (Path(root) / name).lstat().st_size
        for root, _, names in os.walk(path)
        for name in names
    )
