import subprocess
from pathlib import Path

import pytest

from secondshift.repositories.materialization import (
    RepositoryMaterializationError,
    materialize_local_repository,
)


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def local_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.email", "test@example.com")
    git(source, "config", "user.name", "Test")
    (source / "README.md").write_text("first\n")
    git(source, "add", "README.md")
    git(source, "commit", "-m", "first")
    return source, git(source, "rev-parse", "HEAD")


def test_materialize_local_repository_creates_and_updates_a_bare_mirror(
    tmp_path: Path,
) -> None:
    source, first_revision = local_repository(tmp_path)
    runner_root = tmp_path / "runner" / "repositories"

    mirror = materialize_local_repository(
        source=source,
        repository_name="local/project-12345678",
        commit_sha=first_revision,
        repository_root=runner_root,
    )
    (source / "README.md").write_text("second\n")
    git(source, "commit", "-am", "second")
    second_revision = git(source, "rev-parse", "HEAD")
    same_mirror = materialize_local_repository(
        source=source,
        repository_name="local/project-12345678",
        commit_sha=second_revision,
        repository_root=runner_root,
    )

    assert mirror == same_mirror
    assert git(mirror, "rev-parse", "--is-bare-repository") == "true"
    assert str(source) not in (mirror / "config").read_text()
    assert git(mirror, "cat-file", "-t", first_revision) == "commit"
    assert git(mirror, "cat-file", "-t", second_revision) == "commit"


def test_materialization_rejects_names_and_targets_outside_its_owned_store(
    tmp_path: Path,
) -> None:
    source, revision = local_repository(tmp_path)
    runner_root = tmp_path / "runner" / "repositories"

    with pytest.raises(RepositoryMaterializationError, match="escaped"):
        materialize_local_repository(
            source=source,
            repository_name="../outside",
            commit_sha=revision,
            repository_root=runner_root,
        )

    target = runner_root / "local" / "occupied"
    target.mkdir(parents=True)
    with pytest.raises(RepositoryMaterializationError, match="bare Git mirror"):
        materialize_local_repository(
            source=source,
            repository_name="local/occupied",
            commit_sha=revision,
            repository_root=runner_root,
        )
