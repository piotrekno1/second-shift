"""Build the files of one commit into a snapshot a run can unpack.

A snapshot is a gzip tar of the commit's tree, built from Git objects directly.
It does not use `git archive`, because that follows the repository's own
.gitattributes, and `export-ignore` or `export-subst` could make the files a
run gets differ from the commit. The same commit always gives the same bytes,
so a retried run start sends exactly what the first attempt sent.
"""

import gzip
import hashlib
import io
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from secondshift.config.constants import (
    MAX_SKILL_FILES,
    MAX_SKILL_SNAPSHOT_BYTES,
    MAX_SKILL_UNPACKED_BYTES,
    MAX_SOURCE_FILES,
    MAX_SOURCE_SNAPSHOT_BYTES,
    MAX_SOURCE_UNPACKED_BYTES,
)

REGULAR_FILE_MODES = {"100644": 0o644, "100755": 0o755}
SYMLINK_MODE = "120000"
SUBMODULE_MODE = "160000"
COMMIT_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotLimits:
    files: int
    unpacked_bytes: int
    snapshot_bytes: int
    # What the snapshot is of, in error messages.
    label: str
    # A repository keeps its links and submodules. A skill from another
    # repository may hold only regular files.
    regular_files_only: bool = False


REPOSITORY_LIMITS = SnapshotLimits(
    MAX_SOURCE_FILES,
    MAX_SOURCE_UNPACKED_BYTES,
    MAX_SOURCE_SNAPSHOT_BYTES,
    "The commit",
)
SKILL_LIMITS = SnapshotLimits(
    MAX_SKILL_FILES,
    MAX_SKILL_UNPACKED_BYTES,
    MAX_SKILL_SNAPSHOT_BYTES,
    "The skill",
    regular_files_only=True,
)


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    object_id: str
    size: int
    path: str


def tree_entries(git_dir: Path, commit_sha: str, folder: str = "") -> list[TreeEntry]:
    """Every file, link and submodule in the commit, in Git's order.

    With a folder, only what is inside it, with paths relative to it.
    """
    if not COMMIT_SHA.fullmatch(commit_sha):
        raise SnapshotError("A full commit SHA is required.")
    tree = f"{commit_sha}:{folder}" if folder else f"{commit_sha}^{{tree}}"
    output = run_git_bytes(git_dir, "ls-tree", "-r", "-z", "-l", "--full-tree", tree)
    entries = []
    for record in output.split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, _, object_id, size = header.decode().split()
        entries.append(
            TreeEntry(
                mode=mode,
                object_id=object_id,
                size=0 if size == "-" else int(size),
                path=path.decode("utf-8", errors="surrogateescape"),
            )
        )
    return entries


def check_limits(entries: list[TreeEntry], limits: SnapshotLimits) -> None:
    if len(entries) > limits.files:
        raise SnapshotError(f"{limits.label} has more than {limits.files:,} files.")
    if sum(entry.size for entry in entries) > limits.unpacked_bytes:
        raise SnapshotError(f"{limits.label}'s files are too large to run.")
    if limits.regular_files_only and (
        links := [
            entry.path for entry in entries if entry.mode not in REGULAR_FILE_MODES
        ]
    ):
        raise SnapshotError(
            f"{limits.label} may hold only regular files. Not allowed: "
            + ", ".join(links[:5])
        )


def folder_digest(git_dir: Path, entries: list[TreeEntry]) -> str:
    """One SHA-256 for a folder of regular files, as `sha256:<hex>`.

    It hashes the lines `sha256sum` prints for each file, sorted by path, so
    anyone can check it from a checkout without SecondShift. Only names and
    contents count, not file modes. Raises SnapshotError for anything that
    is not a regular file.
    """
    lines = []
    with BlobReader(git_dir) as blobs:
        for entry in entries:
            if entry.mode not in REGULAR_FILE_MODES:
                raise SnapshotError(f"{entry.path} is not a regular file.")
            content = blobs.read(entry.object_id, entry.size)
            path = entry.path.encode("utf-8", errors="surrogateescape")
            lines.append(hashlib.sha256(content).hexdigest().encode() + b"  " + path)
    lines.sort(key=lambda line: line[66:])
    listing = b"".join(line + b"\n" for line in lines)
    return f"sha256:{hashlib.sha256(listing).hexdigest()}"


def write_snapshot(
    git_dir: Path,
    commit_sha: str,
    output: IO[bytes],
    folder: str = "",
    limits: SnapshotLimits = REPOSITORY_LIMITS,
) -> None:
    """Write the commit's files, or one folder's, to `output` as a
    reproducible .tar.gz."""
    entries = tree_entries(git_dir, commit_sha, folder)
    check_limits(entries, limits)
    limited = LimitedWriter(output, limits.snapshot_bytes, limits.label)
    with (
        gzip.GzipFile(
            fileobj=limited, mode="wb", mtime=0, filename="", compresslevel=6
        ) as compressed,
        tarfile.open(
            fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
        ) as archive,
        BlobReader(git_dir) as blobs,
    ):
        for entry in entries:
            info = tarfile.TarInfo(entry.path)
            info.mtime = 0
            if entry.mode in REGULAR_FILE_MODES:
                info.mode = REGULAR_FILE_MODES[entry.mode]
                info.size = entry.size
                archive.addfile(info, blobs.stream(entry.object_id, entry.size))
                blobs.finish()
            elif entry.mode == SYMLINK_MODE:
                info.type = tarfile.SYMTYPE
                info.mode = 0o777
                info.linkname = blobs.read(entry.object_id, entry.size).decode(
                    "utf-8", errors="surrogateescape"
                )
                archive.addfile(info)
            elif entry.mode == SUBMODULE_MODE:
                # Submodule contents are not fetched. Like a checkout without
                # them, the folder exists but is empty.
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                raise SnapshotError(f"Unsupported Git file mode {entry.mode}.")


class LimitedWriter(io.RawIOBase):
    def __init__(self, output: IO[bytes], limit: int, label: str) -> None:
        self.output = output
        self.limit = limit
        self.remaining = limit
        self.label = label

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:  # type: ignore[override]
        self.remaining -= len(data)
        if self.remaining < 0:
            raise SnapshotError(
                f"{self.label}'s files are larger than "
                f"{self.limit // (1024 * 1024)} MiB compressed."
            )
        return self.output.write(data)


class BlobReader:
    """Reads blobs through one `git cat-file --batch` process."""

    def __init__(self, git_dir: Path) -> None:
        self.git_dir = git_dir
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "BlobReader":
        self.process = subprocess.Popen(
            ["git", "-C", str(self.git_dir), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self.process is None:
            return
        if self.process.stdin:
            self.process.stdin.close()
        self.process.kill()
        self.process.wait(timeout=10)
        if self.process.stdout:
            self.process.stdout.close()

    def stream(self, object_id: str, size: int) -> IO[bytes]:
        """The blob's content. Read exactly `size` bytes, then call finish()."""
        process = self.process
        if process is None or process.stdin is None or process.stdout is None:
            raise SnapshotError("The blob reader is not open.")
        process.stdin.write(f"{object_id}\n".encode())
        process.stdin.flush()
        header = process.stdout.readline().decode().split()
        if len(header) != 3 or header[1] != "blob" or int(header[2]) != size:
            raise SnapshotError(f"Git could not read object {object_id}.")
        return process.stdout

    def finish(self) -> None:
        # cat-file ends each object with a newline.
        if self.process and self.process.stdout:
            self.process.stdout.read(1)

    def read(self, object_id: str, size: int) -> bytes:
        content = self.stream(object_id, size).read(size)
        self.finish()
        return content


def run_git_bytes(git_dir: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(git_dir), *arguments],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise SnapshotError("Git timed out while reading the commit.") from error
    if completed.returncode:
        raise SnapshotError("Git could not read the commit.")
    return completed.stdout
