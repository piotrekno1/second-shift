"""The archive a session hands back when the runner collects it.

The runner builds it, and the control plane reads it. Both check every name
and size again.
"""

import hashlib
from pathlib import PurePosixPath
from typing import IO

MANIFEST_NAME = "manifest.json"
CHANGES_NAME = "changes.patch"
# The folder in the archive, and beside the workspace, for saved files.
OUTPUTS_DIR = "outputs"
# A long list of names helps nobody. The patch itself holds every path.
MAX_LISTED_CHANGES = 1000


def safe_output_name(name: str) -> bool:
    """A relative path of plain parts, safe to use as a storage path."""
    path = PurePosixPath(name)
    return (
        0 < len(name) <= 255
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and all(char.isprintable() and char != "\\" for char in name)
    )


class HashingReader:
    """Passes a stream to tarfile and keeps the SHA-256 of what it read."""

    def __init__(self, stream: IO[bytes]) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self.stream.read(size)
        self.digest.update(chunk)
        return chunk

    # Storage backends probe these before saving. django-storages' S3 backend
    # asks whether the file can seek, which reads `closed`. Seeking would skip
    # bytes the hash must see, so the reader never seeks.
    @property
    def closed(self) -> bool:
        return bool(getattr(self.stream, "closed", False))

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        """The archive owns the stream, so closing the reader leaves it open."""
