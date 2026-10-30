"""Source snapshots the control plane uploads for a run to unpack.

The runner keeps no repositories. Each snapshot is named by its SHA-256, kept
only until its session starts, and swept after an hour if no session uses it.
"""

import hashlib
import os
import re
import tarfile
import tempfile
import time
from pathlib import Path
from typing import IO

from django.conf import settings

from secondshift.config.constants import (
    MAX_SOURCE_FILES,
    MAX_SOURCE_SNAPSHOT_BYTES,
    MAX_SOURCE_UNPACKED_BYTES,
)
from secondshift.runner.protocol import RunnerProtocolError

SOURCE_SHA256 = re.compile(r"[0-9a-f]{64}")
SOURCE_MAX_AGE_SECONDS = 60 * 60
CHUNK_BYTES = 1024 * 1024


def sources_root() -> Path:
    return (settings.RUNNER_WORK_ROOT / "sources").resolve()


def source_path(sha256: str) -> Path:
    if not SOURCE_SHA256.fullmatch(sha256):
        raise RunnerProtocolError("A source is named by its SHA-256.")
    return sources_root() / f"{sha256}.tar.gz"


def has_source(sha256: str) -> bool:
    """Whether the snapshot is here. Asking keeps it for another hour."""
    path = source_path(sha256)
    try:
        os.utime(path)
    except FileNotFoundError:
        return False
    return True


def receive_source(sha256: str, stream: IO[bytes], length: int | None) -> None:
    """Store an uploaded snapshot if its bytes match its name."""
    target = source_path(sha256)
    if length is None:
        raise RunnerProtocolError("The source upload needs a Content-Length.")
    if length > MAX_SOURCE_SNAPSHOT_BYTES:
        raise RunnerProtocolError("The source is too large.")
    root = sources_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    sweep_sources()
    descriptor, name = tempfile.mkstemp(dir=root, prefix="upload-")
    temporary = Path(name)
    try:
        digest = hashlib.sha256()
        remaining = length
        with os.fdopen(descriptor, "wb") as output:
            while remaining:
                chunk = stream.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise RunnerProtocolError("The source upload ended early.")
                remaining -= len(chunk)
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != sha256:
            raise RunnerProtocolError("The source does not match its SHA-256.")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def sweep_sources() -> None:
    """Remove snapshots and partial uploads no session started with."""
    root = sources_root()
    if not root.is_dir():
        return
    cutoff = time.time() - SOURCE_MAX_AGE_SECONDS
    for path in root.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            continue


def unpack_source(sha256: str, workspace: Path) -> None:
    """Unpack an uploaded snapshot into a session's empty workspace."""
    path = source_path(sha256)
    if not path.is_file():
        raise RunnerProtocolError("The run's source files were not uploaded.")
    if file_sha256(path) != sha256:
        raise RunnerProtocolError("The run's source files do not match their SHA-256.")
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            check_members(archive)
            archive.extractall(workspace, filter="data")
    except (tarfile.TarError, OSError, EOFError) as error:
        raise RunnerProtocolError(
            "The runner could not unpack the run's source files."
        ) from error


def check_members(archive: tarfile.TarFile) -> None:
    # Stop at the first entry past a limit, so a compression bomb is never
    # read to the end.
    size = 0
    for files, member in enumerate(archive, start=1):
        size += member.size
        if files > MAX_SOURCE_FILES or size > MAX_SOURCE_UNPACKED_BYTES:
            raise RunnerProtocolError("The run's source files are too large.")
        if not (member.isreg() or member.isdir() or member.issym()):
            raise RunnerProtocolError(
                "The run's source files may only hold files, folders and links."
            )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
