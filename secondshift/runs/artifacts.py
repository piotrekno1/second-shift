"""Run inputs and outputs.

Full runner output lives in the private ``run_artifacts`` storage, never in
Postgres or DBOS checkpoints. The database keeps metadata and a short excerpt.
"""

import hashlib
import json
import tarfile
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path
from typing import IO, Any

from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage, Storage, storages

from secondshift.config.constants import (
    MAX_CHANGES_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_FILES,
)
from secondshift.runner.collection import (
    CHANGES_NAME,
    MANIFEST_NAME,
    MAX_LISTED_CHANGES,
    OUTPUTS_DIR,
    HashingReader,
    safe_output_name,
)
from secondshift.runs.models import Run
from secondshift.runs.outputs import Changes, RunOutputs, SavedFile, SkippedFile

TRUNCATION_MARKER = "\n\n[... {omitted} bytes omitted by SecondShift ...]\n\n"


@dataclass(frozen=True)
class StoredOutput:
    path: str
    size_bytes: int
    sha256: str
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def artifact_storage() -> Storage:
    return storages["run_artifacts"]


def run_artifact_prefix(run: Run) -> str:
    """Partition by workspace, then by the run's creation day (UTC).

    A workspace's artifacts share one prefix, so deleting a tenant is one
    prefix delete; within it, age-based sweeps can drop whole day folders. The
    path is derived from stored fields only, so DBOS retries rewrite the same
    object instead of creating a new one.
    """
    created = run.created_at.astimezone(UTC)
    return f"runs/{run.workspace_id}/{created:%Y/%m/%d}/{run.pk}"


def output_path(run: Run) -> str:
    return f"{run_artifact_prefix(run)}/output.log"


def input_path(run: Run) -> str:
    return f"{run_artifact_prefix(run)}/input.md"


def payload_path(run: Run) -> str:
    return f"{run_artifact_prefix(run)}/payload.txt"


class CollectionError(ValueError):
    pass


def changes_path(run: Run) -> str:
    return f"{run_artifact_prefix(run)}/{CHANGES_NAME}"


def saved_file_path(run: Run, name: str) -> str:
    return f"{run_artifact_prefix(run)}/{OUTPUTS_DIR}/{name}"


def store_collection(run: Run, archive_path: Path) -> RunOutputs:
    """Keep what the runner collected, and return what Run.outputs records.

    The archive comes from the trusted runner, but the agent chose its file
    names and contents. Only the expected names and regular files are kept,
    within the same limits the runner applies.
    """
    stored_changes: dict[str, Any] = {}
    files: list[SavedFile] = []
    manifest: dict[str, Any] = {}
    total = 0
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for count, member in enumerate(archive):
            if count > MAX_OUTPUT_FILES + 2 or not member.isreg():
                raise CollectionError("The collected files are not valid.")
            stream = archive.extractfile(member)
            assert stream is not None
            if member.name == MANIFEST_NAME:
                manifest = json.loads(stream.read(1024 * 1024))
                continue
            if member.name == CHANGES_NAME and member.size <= MAX_CHANGES_BYTES:
                stored_changes = store_stream(changes_path(run), stream, member.size)
                continue
            name = member.name.removeprefix(f"{OUTPUTS_DIR}/")
            total += member.size
            if (
                name == member.name
                or not safe_output_name(name)
                or total > MAX_OUTPUT_BYTES
            ):
                raise CollectionError("The collected files are not valid.")
            stored = store_stream(saved_file_path(run, name), stream, member.size)
            files.append(SavedFile(name=name, **stored))
    changes = manifest.get("changes") or {}
    return RunOutputs(
        changes=Changes(
            file_count=int(changes.get("file_count", 0)),
            files=tuple(str(item) for item in changes.get("files", []))[
                :MAX_LISTED_CHANGES
            ],
            too_large=bool(changes.get("too_large")),
            **stored_changes,
        ),
        files=tuple(files),
        skipped=tuple(
            SkippedFile(
                name=str(item.get("name", ""))[:255],
                reason=str(item.get("reason", ""))[:200],
            )
            for item in manifest.get("skipped", [])[:MAX_OUTPUT_FILES]
        ),
    )


def store_stream(path: str, stream: IO[bytes], size: int) -> dict[str, Any]:
    """Save at a retry-stable path, and return the stored path, size and hash."""
    reader = HashingReader(stream)
    storage = artifact_storage()
    storage.delete(path)
    saved = storage.save(path, File(reader))
    return {"path": saved, "size_bytes": size, "sha256": reader.digest.hexdigest()}


def run_output_paths(run: Run) -> list[str]:
    """Every stored file the sandbox left behind, for deletion with the run."""
    return run.collected.stored_paths() if run.collected else []


def store_run_payload(run: Run, body: bytes) -> dict[str, Any]:
    """Keep the request body that started a run, cut to TRIGGER_PAYLOAD_MAX_BYTES.

    Returns what the run's input snapshot records about it.
    """
    kept = body[: settings.TRIGGER_PAYLOAD_MAX_BYTES]
    storage = artifact_storage()
    path = payload_path(run)
    storage.delete(path)
    saved = storage.save(path, ContentFile(kept))
    return {
        "path": saved,
        "size_bytes": len(body),
        "kept_bytes": len(kept),
        "truncated": len(kept) < len(body),
    }


def run_payload(run: Run) -> dict[str, Any] | None:
    """What the input snapshot records about the run's payload, if it has one."""
    return run.input_snapshot.get("payload")


def store_run_input(run: Run, text: str) -> StoredOutput:
    """Store the rendered input, bounded like output, at a retry-stable path."""
    raw = text.encode()
    size = len(raw)
    truncated = size > settings.RUN_OUTPUT_MAX_BYTES
    if truncated:
        raw = raw[: settings.RUN_OUTPUT_MAX_BYTES]
    storage = artifact_storage()
    path = input_path(run)
    storage.delete(path)
    saved = storage.save(path, ContentFile(raw))
    return StoredOutput(
        path=saved,
        size_bytes=size,
        sha256=hashlib.sha256(raw).hexdigest(),
        truncated=truncated,
    )


def store_run_text(run: Run, output: str) -> StoredOutput:
    """Store a model's final report without putting it in a temporary file."""
    raw = output.encode()
    size = len(raw)
    truncated = size > settings.RUN_OUTPUT_MAX_BYTES
    if truncated:
        half = settings.RUN_OUTPUT_MAX_BYTES // 2
        marker = TRUNCATION_MARKER.format(omitted=size - 2 * half).encode()
        raw = raw[:half] + marker + raw[-half:]
    storage = artifact_storage()
    path = output_path(run)
    storage.delete(path)
    saved = storage.save(path, ContentFile(raw))
    return StoredOutput(
        path=saved,
        size_bytes=size,
        sha256=hashlib.sha256(raw).hexdigest(),
        truncated=truncated,
    )


def read_output_excerpt(path: str) -> str:
    """Return the end of a stored output, where the final result usually is."""
    if not path:
        return ""
    limit = settings.RUN_OUTPUT_EXCERPT_BYTES
    storage = artifact_storage()
    with storage.open(path, "rb") as stream:
        size = storage.size(path)
        if size > limit:
            stream.seek(size - limit)
        return stream.read().decode(errors="replace")


def delete_output(path: str) -> None:
    """Delete a stored output. Runs call this through the post_delete signal."""
    storage = artifact_storage()
    storage.delete(path)
    if isinstance(storage, FileSystemStorage):
        remove_empty_parents(Path(storage.path(path)).parent, Path(storage.location))


def remove_empty_parents(directory: Path, root: Path) -> None:
    """Drop folders emptied by a delete, so day partitions do not pile up.

    Object stores have no folders, so this only applies to the filesystem.
    """
    root = root.resolve()
    directory = directory.resolve()
    while directory != root and directory.is_relative_to(root):
        try:
            directory.rmdir()
        except OSError:  # not empty, or already gone
            return
        directory = directory.parent


def read_text(path: str, max_bytes: int) -> tuple[str, bool]:
    """The start of a stored file, and whether it was cut at max_bytes."""
    storage = artifact_storage()
    with storage.open(path, "rb") as stream:
        raw = stream.read(max_bytes + 1)
    return raw[:max_bytes].decode(errors="replace"), len(raw) > max_bytes
