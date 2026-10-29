import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from secondshift.repositories import snapshots
from secondshift.repositories.snapshots import SnapshotError, write_snapshot


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.email", "test@example.com")
    git(source, "config", "user.name", "Test")
    (source / "README.md").write_text("first\n")
    (source / "scripts").mkdir()
    (source / "scripts" / "check.sh").write_text("#!/bin/sh\necho ok\n")
    (source / "scripts" / "check.sh").chmod(0o755)
    (source / "hidden.txt").write_text("hidden from git archive\n")
    (source / "stamp.txt").write_text("$Format:%H$\n")
    (source / ".gitattributes").write_text(
        "hidden.txt export-ignore\nstamp.txt export-subst\n"
    )
    (source / "link").symlink_to("README.md")
    git(source, "add", ".")
    git(source, "commit", "-m", "first")
    return source, git(source, "rev-parse", "HEAD")


def snapshot_bytes(git_dir: Path, commit_sha: str) -> bytes:
    output = io.BytesIO()
    write_snapshot(git_dir, commit_sha, output)
    return output.getvalue()


def members(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        return {member.name: member for member in archive.getmembers()}


def read_file(data: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        extracted = archive.extractfile(name)
        assert extracted is not None
        return extracted.read()


def test_snapshot_holds_exactly_the_commits_files(tmp_path: Path) -> None:
    source, revision = repository(tmp_path)
    (source / "README.md").write_text("uncommitted\n")

    data = snapshot_bytes(source, revision)

    found = members(data)
    assert set(found) == {
        ".gitattributes",
        "README.md",
        "hidden.txt",
        "link",
        "scripts/check.sh",
        "stamp.txt",
    }
    assert read_file(data, "README.md") == b"first\n"
    # .gitattributes cannot hide or rewrite files.
    assert read_file(data, "hidden.txt") == b"hidden from git archive\n"
    assert read_file(data, "stamp.txt") == b"$Format:%H$\n"
    assert found["scripts/check.sh"].mode == 0o755
    assert found["README.md"].mode == 0o644
    assert found["link"].issym() and found["link"].linkname == "README.md"


def test_the_same_commit_gives_the_same_bytes(tmp_path: Path) -> None:
    source, revision = repository(tmp_path)

    first = snapshot_bytes(source, revision)
    clone = tmp_path / "clone.git"
    subprocess.run(
        ["git", "clone", "--bare", "--quiet", str(source), str(clone)], check=True
    )

    assert snapshot_bytes(clone, revision) == first


def test_submodules_become_empty_folders(tmp_path: Path) -> None:
    source, _ = repository(tmp_path)
    git(
        source,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{'b' * 40},vendor/library",
    )
    git(source, "commit", "-m", "submodule")

    found = members(snapshot_bytes(source, git(source, "rev-parse", "HEAD")))

    assert found["vendor/library"].isdir()


@pytest.mark.parametrize("commit_sha", ["HEAD", "--output=/tmp/x", "a" * 39])
def test_only_full_commit_ids_reach_git(tmp_path: Path, commit_sha: str) -> None:
    source, _ = repository(tmp_path)

    with pytest.raises(SnapshotError, match="full commit SHA"):
        snapshot_bytes(source, commit_sha)


def test_an_unknown_commit_fails(tmp_path: Path) -> None:
    source, _ = repository(tmp_path)

    with pytest.raises(SnapshotError, match="could not read"):
        snapshot_bytes(source, "c" * 40)


def test_limits_apply_while_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, revision = repository(tmp_path)

    monkeypatch.setattr(snapshots, "MAX_SOURCE_FILES", 2)
    with pytest.raises(SnapshotError, match="more than 2 files"):
        snapshot_bytes(source, revision)
    monkeypatch.undo()

    monkeypatch.setattr(snapshots, "MAX_SOURCE_UNPACKED_BYTES", 10)
    with pytest.raises(SnapshotError, match="too large"):
        snapshot_bytes(source, revision)
    monkeypatch.undo()

    monkeypatch.setattr(snapshots, "MAX_SOURCE_SNAPSHOT_BYTES", 100)
    with pytest.raises(SnapshotError, match="compressed"):
        snapshot_bytes(source, revision)
