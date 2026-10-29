import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from secondshift.repositories import sources
from secondshift.repositories.models import Repository
from secondshift.repositories.snapshots import SnapshotError
from secondshift.repositories.sources import source_snapshot


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
