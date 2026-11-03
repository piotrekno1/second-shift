import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from secondshift.runner import collection
from secondshift.runner.collection import collect, record_baseline


@pytest.fixture
def session(tmp_path: Path) -> tuple[Path, Path]:
    """A session folder whose workspace holds a small committed project."""
    root = tmp_path / "session"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (root / collection.OUTPUTS_DIR).mkdir()
    (workspace / "README.md").write_text("# Project\n\nOld setup step.\n")
    (workspace / "remove-me.txt").write_text("bye\n")
    (workspace / ".gitignore").write_text("build/\n*.log\n")
    # Committed even though it matches .gitignore, as git archive allows.
    (workspace / "kept.log").write_text("tracked log\n")
    (workspace / "logo.bin").write_bytes(bytes(range(256)))
    record_baseline(root, workspace)
    return root, workspace


def read_archive(path: Path) -> tuple[dict, dict[str, bytes]]:
    with tarfile.open(path, mode="r:gz") as archive:
        contents = {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive
        }
    return json.loads(contents.pop(collection.MANIFEST_NAME)), contents


def edit_like_an_agent(workspace: Path) -> None:
    (workspace / "README.md").write_text("# Project\n\nNew setup step.\n")
    (workspace / "remove-me.txt").unlink()
    (workspace / "docs").mkdir()
    (workspace / "docs" / "guide.md").write_text("A new guide.\n")
    (workspace / "kept.log").write_text("tracked log, edited\n")
    (workspace / "logo.bin").write_bytes(bytes(reversed(range(256))))
    # Build output and new ignored files are not changes.
    (workspace / "build").mkdir()
    (workspace / "build" / "app.js").write_text("compiled\n")
    (workspace / "debug.log").write_text("noise\n")


def test_changes_are_a_patch_that_reproduces_the_workspace(
    session: tuple[Path, Path], tmp_path: Path
) -> None:
    root, workspace = session
    pristine = tmp_path / "pristine"
    shutil.copytree(workspace, pristine)
    edit_like_an_agent(workspace)

    manifest, contents = read_archive(collect(root, workspace))

    changes = manifest["changes"]
    assert changes["captured"] is True
    assert sorted(changes["files"]) == [
        "README.md",
        "docs/guide.md",
        "kept.log",
        "logo.bin",
        "remove-me.txt",
    ]
    assert changes["file_count"] == 5
    patch = tmp_path / "changes.patch"
    patch.write_bytes(contents[collection.CHANGES_NAME])
    subprocess.run(["git", "apply", "--binary", str(patch)], cwd=pristine, check=True)
    for name in ("README.md", "docs/guide.md", "kept.log", "logo.bin"):
        assert (pristine / name).read_bytes() == (workspace / name).read_bytes()
    assert not (pristine / "remove-me.txt").exists()
    assert not (pristine / "build").exists()


def test_no_changes_means_no_patch(session: tuple[Path, Path]) -> None:
    root, workspace = session

    manifest, contents = read_archive(collect(root, workspace))

    assert manifest["changes"]["captured"] is False
    assert manifest["changes"]["file_count"] == 0
    assert collection.CHANGES_NAME not in contents


def test_changes_past_the_limit_are_described_but_not_kept(
    session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, workspace = session
    monkeypatch.setattr(collection, "MAX_CHANGES_BYTES", 10)
    edit_like_an_agent(workspace)

    manifest, contents = read_archive(collect(root, workspace))

    assert manifest["changes"]["too_large"] is True
    assert manifest["changes"]["captured"] is False
    assert collection.CHANGES_NAME not in contents


def test_the_workspace_cannot_configure_or_replace_the_baseline(
    session: tuple[Path, Path], tmp_path: Path
) -> None:
    root, workspace = session
    marker = tmp_path / "ran"
    # A repository of the agent's own, with settings that would run commands.
    fake = workspace / ".git"
    fake.mkdir()
    (fake / "config").write_text(
        f"[core]\n\tfsmonitor = touch {marker}\n\thooksPath = hooks\n"
        f'[diff "evil"]\n\tcommand = touch {marker}\n'
        f'[filter "evil"]\n\tclean = touch {marker}\n'
    )
    (workspace / ".gitattributes").write_text("* diff=evil filter=evil\n")
    (workspace / "README.md").write_text("changed\n")

    manifest, contents = read_archive(collect(root, workspace))

    assert not marker.exists()
    assert "README.md" in manifest["changes"]["files"]
    assert ".gitattributes" in manifest["changes"]["files"]
    assert not any(name.startswith(".git/") for name in manifest["changes"]["files"])
    assert b"changed" in contents[collection.CHANGES_NAME]


def test_saved_files_are_kept_and_links_are_refused(
    session: tuple[Path, Path], tmp_path: Path
) -> None:
    root, workspace = session
    outputs = root / collection.OUTPUTS_DIR
    (outputs / "report.csv").write_text("a,b\n1,2\n")
    (outputs / "charts").mkdir()
    (outputs / "charts" / "trend.png").write_bytes(b"\x89PNG fake")
    secret = tmp_path / "host-secret.txt"
    secret.write_text("runner credentials")
    (outputs / "steal.txt").symlink_to(secret)
    (outputs / "folder-link").symlink_to(tmp_path, target_is_directory=True)

    manifest, contents = read_archive(collect(root, workspace))

    assert [item["name"] for item in manifest["outputs"]] == [
        "report.csv",
        "charts/trend.png",
    ]
    assert contents["outputs/report.csv"] == b"a,b\n1,2\n"
    assert contents["outputs/charts/trend.png"] == b"\x89PNG fake"
    assert {item["name"] for item in manifest["skipped"]} == {
        "steal.txt",
        "folder-link",
    }
    assert b"runner credentials" not in b"".join(contents.values())


def test_saved_files_stop_at_the_count_and_size_limits(
    session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, workspace = session
    monkeypatch.setattr(collection, "MAX_OUTPUT_FILES", 2)
    monkeypatch.setattr(collection, "MAX_OUTPUT_BYTES", 10)
    outputs = root / collection.OUTPUTS_DIR
    for name, size in (("a.txt", 4), ("b.txt", 20), ("c.txt", 4), ("d.txt", 1)):
        (outputs / name).write_bytes(b"x" * size)

    manifest, _ = read_archive(collect(root, workspace))

    assert [item["name"] for item in manifest["outputs"]] == ["a.txt", "c.txt"]
    assert [item["reason"] for item in manifest["skipped"]] == [
        "The files are larger than the limit.",
        "Only the first 2 files are kept.",
    ]


def test_collecting_again_returns_the_same_archive(
    session: tuple[Path, Path],
) -> None:
    root, workspace = session
    (workspace / "README.md").write_text("first\n")
    first = collect(root, workspace)
    content = first.read_bytes()
    (workspace / "README.md").write_text("second\n")

    assert collect(root, workspace).read_bytes() == content


def test_the_baseline_lives_outside_the_workspace(
    session: tuple[Path, Path],
) -> None:
    root, workspace = session

    assert (root / collection.BASELINE_DIR / "HEAD").is_file()
    assert not (workspace / ".git").exists()


def test_a_session_without_a_baseline_still_returns_saved_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "session"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (root / collection.OUTPUTS_DIR).mkdir()
    (workspace / "README.md").write_text("edited, but nobody asked for changes\n")
    (root / collection.OUTPUTS_DIR / "report.csv").write_text("a\n")

    manifest, contents = read_archive(collect(root, workspace))

    assert manifest["changes"] == collection.NO_CHANGES
    assert collection.CHANGES_NAME not in contents
    assert contents["outputs/report.csv"] == b"a\n"
