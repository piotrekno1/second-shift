import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone

from secondshift.repositories import sources
from secondshift.repositories.mirrors import locked_mirror
from secondshift.repositories.models import Repository
from secondshift.repositories.snapshots import SnapshotError
from secondshift.repositories.sources import prune_source_cache, source_snapshot


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "upstream"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.email", "test@example.com")
    git(source, "config", "user.name", "Test")
    (source / "README.md").write_text("first\n")
    git(source, "add", "README.md")
    git(source, "commit", "-m", "first")
    return source, git(source, "rev-parse", "HEAD")


@pytest.fixture
def github_repository(workspace: Any, settings: Any, tmp_path: Path) -> Repository:
    settings.SOURCE_CACHE_ROOT = tmp_path / "sources"
    return Repository.objects.create(workspace=workspace, full_name="owner/project")


def test_a_missing_commit_is_fetched_once_then_cached(
    github_repository: Repository,
    upstream: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, revision = upstream
    fetched = []

    def fetch(repository: Repository, commit_sha: str, mirror: Path) -> None:
        fetched.append(commit_sha)
        git(mirror, "fetch", "--quiet", str(source), commit_sha)

    monkeypatch.setattr(sources, "fetch_github_commit", fetch)

    first = source_snapshot(github_repository, revision)
    second = source_snapshot(github_repository, revision)

    assert fetched == [revision]
    assert first == second
    assert first.path.name == f"{revision}.tar.gz"


def test_a_wiped_cache_rebuilds_the_same_snapshot(
    github_repository: Repository,
    upstream: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> None:
    source, revision = upstream
    monkeypatch.setattr(
        sources,
        "fetch_github_commit",
        lambda repository, sha, mirror: git(
            mirror, "fetch", "--quiet", str(source), sha
        ),
    )
    first = source_snapshot(github_repository, revision)

    shutil.rmtree(settings.SOURCE_CACHE_ROOT)

    assert source_snapshot(github_repository, revision).sha256 == first.sha256


def test_local_snapshots_need_local_repositories_enabled(
    workspace: Any, upstream: tuple[Path, str], settings: Any, tmp_path: Path
) -> None:
    source, revision = upstream
    settings.SOURCE_CACHE_ROOT = tmp_path / "sources"
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="local/project",
        source_type=Repository.SourceType.LOCAL,
        local_path=str(source),
    )
    settings.LOCAL_REPOSITORIES_ENABLED = False

    with pytest.raises(SnapshotError, match="single host"):
        source_snapshot(repository, revision)

    settings.LOCAL_REPOSITORIES_ENABLED = True
    assert source_snapshot(repository, revision).size_bytes > 0


def test_a_commit_must_be_a_full_sha(github_repository: Repository) -> None:
    with pytest.raises(SnapshotError, match="full commit SHA"):
        source_snapshot(github_repository, "main")


def fake_snapshot(settings: Any, repository_id: int, name: str, size: int, age: int):
    path = settings.SOURCE_CACHE_ROOT / "snapshots" / str(repository_id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    when = time.time() - age
    os.utime(path, (when, when))
    return path


def test_snapshots_are_trimmed_oldest_first(
    github_repository: Repository, settings: Any
) -> None:
    settings.SOURCE_CACHE_MAX_BYTES = 25
    oldest = fake_snapshot(settings, github_repository.pk, "a.tar.gz", 10, 300)
    middle = fake_snapshot(settings, github_repository.pk, "b.tar.gz", 10, 200)
    newest = fake_snapshot(settings, github_repository.pk, "c.tar.gz", 10, 100)

    planned = prune_source_cache(dry_run=True)

    assert (planned.snapshots, planned.bytes) == (1, 10)
    assert oldest.exists()

    prune_source_cache()

    assert not oldest.exists()
    assert middle.exists() and newest.exists()


def test_removed_repositories_lose_their_cache(
    github_repository: Repository, settings: Any, workspace: Any
) -> None:
    kept = fake_snapshot(settings, github_repository.pk, "a.tar.gz", 10, 0)
    removed = Repository.objects.create(
        workspace=workspace, full_name="owner/removed", removed_at=timezone.now()
    )
    gone = fake_snapshot(settings, removed.pk, "a.tar.gz", 10, 0)
    with locked_mirror(removed.pk) as mirror:
        pass
    with locked_mirror(github_repository.pk) as active_mirror:
        pass
    unknown = settings.SOURCE_CACHE_ROOT / "snapshots" / "not-a-repository"
    unknown.mkdir()

    planned = prune_source_cache(dry_run=True)
    assert (planned.snapshots, planned.mirrors) == (1, 1)
    assert gone.exists() and mirror.exists()

    prune_source_cache()

    assert not gone.exists() and not mirror.exists()
    assert kept.exists() and active_mirror.exists()
    # Only entries named by a repository ID are ever touched.
    assert unknown.exists()


def test_crashed_builds_are_removed_after_an_hour(
    github_repository: Repository, settings: Any
) -> None:
    stale = fake_snapshot(settings, github_repository.pk, "building-a", 5, 7200)
    fresh = fake_snapshot(settings, github_repository.pk, "building-b", 5, 0)

    prune_source_cache()

    assert not stale.exists()
    assert fresh.exists()
