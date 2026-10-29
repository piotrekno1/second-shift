"""Build the files of one commit into a snapshot a run can unpack.

A snapshot is a gzip tar of the commit's tree, built from Git objects directly.
It does not use `git archive`, because that follows the repository's own
.gitattributes, and `export-ignore` or `export-subst` could make the files a
run gets differ from the commit. The same commit always gives the same bytes,
so a retried run start sends exactly what the first attempt sent.
"""

import gzip
import io
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from secondshift.config.constants import (
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
class TreeEntry:
    mode: str
    object_id: str
    size: int
    path: str


def tree_entries(git_dir: Path, commit_sha: str) -> list[TreeEntry]:
    """Every file, link and submodule in the commit, in Git's order."""
    if not COMMIT_SHA.fullmatch(commit_sha):
        raise SnapshotError("A full commit SHA is required.")
    output = run_git_bytes(
        git_dir, "ls-tree", "-r", "-z", "-l", "--full-tree", f"{commit_sha}^{{tree}}"
    )
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


def write_snapshot(git_dir: Path, commit_sha: str, output: IO[bytes]) -> None:
    """Write the commit's files to `output` as a reproducible .tar.gz."""
    entries = tree_entries(git_dir, commit_sha)
    if len(entries) > MAX_SOURCE_FILES:
        raise SnapshotError(f"The commit has more than {MAX_SOURCE_FILES:,} files.")
    if sum(entry.size for entry in entries) > MAX_SOURCE_UNPACKED_BYTES:
        raise SnapshotError("The commit's files are too large to run.")
    limited = LimitedWriter(output, MAX_SOURCE_SNAPSHOT_BYTES)
    with (
        gzip.GzipFile(fileobj=limited, mode="wb", mtime=0, filename="") as compressed,
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
    def __init__(self, output: IO[bytes], limit: int) -> None:
        self.output = output
        self.remaining = limit

    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:  # type: ignore[override]
        self.remaining -= len(data)
        if self.remaining < 0:
            raise SnapshotError(
                "The commit's files are larger than "
                f"{MAX_SOURCE_SNAPSHOT_BYTES // (1024 * 1024)} MiB compressed."
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
