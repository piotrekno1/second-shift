"""What a session leaves behind: its file changes and the files it saved.

When a session starts, the runner records the unpacked commit in a git folder
beside the workspace, outside the sandbox's mounts, so the agent cannot change
it. After the agent finishes, the runner stops the sandbox and compares the
workspace with that record. The result is one archive:

- ``manifest.json`` describes the changes and the saved files
- ``changes.patch`` is a binary-safe git diff, if anything changed
- ``outputs/<name>`` holds each regular file the agent saved in /outputs

Git runs here with no system, global or repository settings that the
workspace could supply, and never runs hooks, filters or external diff tools.
"""

import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import IO, Any

from secondshift.config.constants import (
    MAX_CHANGES_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_FILES,
)
from secondshift.runner.protocol import RunnerProtocolError

BASELINE_DIR = "baseline.git"
OUTPUTS_DIR = "outputs"
COLLECTION_FILE = "collection.tar.gz"
MANIFEST_NAME = "manifest.json"
CHANGES_NAME = "changes.patch"
# A long list of names helps nobody. The patch itself holds every path.
MAX_LISTED_CHANGES = 1000
GIT_TIMEOUT_SECONDS = 300


def git(
    session_root: Path,
    workspace: Path,
    *args: str,
    stdout: IO[bytes] | None = None,
    work_tree: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    git_dir = session_root / BASELINE_DIR
    command = [
        "git",
        # The workspace belongs to the sandbox user, not to the runner.
        "-c",
        "safe.directory=*",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.symlinks=true",
        f"--git-dir={git_dir}",
        # git init refuses a work tree. Every other command compares one.
        *([f"--work-tree={workspace}"] if work_tree else []),
        *args,
    ]
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(git_dir),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "SecondShift",
        "GIT_AUTHOR_EMAIL": "runner@secondshift.invalid",
        "GIT_COMMITTER_NAME": "SecondShift",
        "GIT_COMMITTER_EMAIL": "runner@secondshift.invalid",
    }
    try:
        return subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunnerProtocolError("The runner could not compare the files.") from error


def require_git(result: subprocess.CompletedProcess[bytes]) -> None:
    if result.returncode:
        raise RunnerProtocolError("The runner could not compare the files.")


def record_baseline(session_root: Path, workspace: Path) -> None:
    """Commit the unpacked files, before the sandbox can change them."""
    git_dir = session_root / BASELINE_DIR
    git_dir.mkdir(mode=0o700)
    require_git(
        git(session_root, workspace, "init", "--quiet", "--bare", work_tree=False)
    )
    # Forced, so a committed file that matches .gitignore is compared too.
    require_git(git(session_root, workspace, "add", "--all", "--force", "."))
    require_git(
        git(
            session_root,
            workspace,
            "commit",
            "--quiet",
            "--no-verify",
            "--allow-empty",
            "--message=Run baseline",
        )
    )


def collect(session_root: Path, workspace: Path) -> Path:
    """Build the session's archive once. Later calls return the same one.

    The sandbox must already be stopped, so nothing changes the files while
    they are read.
    """
    archive_path = session_root / COLLECTION_FILE
    if archive_path.is_file():
        return archive_path
    partial = session_root / f"{COLLECTION_FILE}.partial"
    patch_path = session_root / CHANGES_NAME
    manifest: dict[str, Any] = {"changes": compare(session_root, workspace, patch_path)}
    with tarfile.open(partial, mode="w:gz") as archive:
        if manifest["changes"]["captured"]:
            add_file(archive, patch_path, CHANGES_NAME)
        manifest["outputs"], manifest["skipped"] = add_outputs(
            archive, session_root / OUTPUTS_DIR
        )
        add_bytes(archive, MANIFEST_NAME, json.dumps(manifest).encode())
    patch_path.unlink(missing_ok=True)
    os.replace(partial, archive_path)
    return archive_path


def compare(session_root: Path, workspace: Path, patch_path: Path) -> dict[str, Any]:
    """Write the changes since the baseline, if they fit, and describe them."""
    # Not forced: new files that match .gitignore, such as build output, stay out.
    require_git(git(session_root, workspace, "add", "--all", "."))
    names = git(session_root, workspace, "diff", "--cached", "--name-only", "-z")
    require_git(names)
    paths = [
        item.decode(errors="replace") for item in names.stdout.split(b"\0") if item
    ]
    with patch_path.open("wb") as stream:
        require_git(
            git(
                session_root,
                workspace,
                "diff",
                "--cached",
                "--binary",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--full-index",
                stdout=stream,
            )
        )
    size = patch_path.stat().st_size
    return {
        "file_count": len(paths),
        "files": paths[:MAX_LISTED_CHANGES],
        "size_bytes": size,
        "captured": bool(paths) and size <= MAX_CHANGES_BYTES,
        "too_large": size > MAX_CHANGES_BYTES,
    }


def add_outputs(
    archive: tarfile.TarFile, outputs: Path
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    saved: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if not outputs.is_dir():
        return saved, skipped
    total = 0
    for root, directories, files in os.walk(outputs, followlinks=False):
        # Linked folders are never entered, and are listed as skipped.
        for name in [item for item in directories if (Path(root) / item).is_symlink()]:
            directories.remove(name)
            relative = (Path(root) / name).relative_to(outputs).as_posix()
            skipped.append({"name": relative, "reason": "Not a regular file."})
        directories.sort()
        for name in sorted(files):
            path = Path(root) / name
            relative = path.relative_to(outputs).as_posix()
            reason = output_problem(path, relative, len(saved), total)
            if reason:
                skipped.append({"name": relative, "reason": reason})
                continue
            size, digest = add_file(archive, path, f"{OUTPUTS_DIR}/{relative}")
            total += size
            saved.append({"name": relative, "size_bytes": size, "sha256": digest})
    return saved, skipped


def output_problem(path: Path, relative: str, count: int, total: int) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        return "Not a regular file."
    if not safe_output_name(relative):
        return "The file name is not allowed."
    if count >= MAX_OUTPUT_FILES:
        return f"Only the first {MAX_OUTPUT_FILES} files are kept."
    if total + info.st_size > MAX_OUTPUT_BYTES:
        return "The files are larger than the limit."
    return ""


def safe_output_name(name: str) -> bool:
    """A relative path of plain parts, safe to use as a storage path."""
    path = PurePosixPath(name)
    return (
        0 < len(name) <= 255
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and all(char.isprintable() and char != "\\" for char in name)
    )


def add_file(archive: tarfile.TarFile, path: Path, name: str) -> tuple[int, str]:
    """Add one regular file under a fixed owner and mode. Links are refused."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise RunnerProtocolError("A collected file is not a regular file.")
        member = tarfile.TarInfo(name)
        member.size = info.st_size
        member.mode = 0o600
        reader = HashingReader(stream)
        archive.addfile(member, reader)
        return info.st_size, reader.digest.hexdigest()


class HashingReader:
    """Passes a stream to tarfile and keeps the SHA-256 of what it read."""

    def __init__(self, stream: IO[bytes]) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self.stream.read(size)
        self.digest.update(chunk)
        return chunk


def add_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = 0o600
    archive.addfile(member, io.BytesIO(content))
