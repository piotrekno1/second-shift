"""Run inputs and outputs.

Full runner output lives in the private ``run_artifacts`` storage, never in
Postgres or DBOS checkpoints. The database keeps metadata and a short excerpt.
"""

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage, storages

from secondshift.automations.models import Job, Run

REDACTED = "[REDACTED]"
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


def job_input_snapshot(job: Job) -> dict[str, Any]:
    """Freeze what a run was asked to do, since the job may change later."""
    return {
        "job_key": job.key,
        "skill_path": job.skill_path,
        "config_hash": job.config_hash,
    }


def secret_variants(value: str) -> list[str]:
    """Return the forms a secret commonly takes when echoed by tools."""
    raw = value.encode()
    variants = {
        value,
        base64.b64encode(raw).decode(),
        base64.urlsafe_b64encode(raw).decode(),
        quote(value, safe=""),
        json.dumps(value)[1:-1],
    }
    variants |= {variant.rstrip("=") for variant in variants}
    # Longest first so a short variant never splits a longer match.
    return sorted((v for v in variants if v), key=len, reverse=True)


def redact(text: str, secrets: dict[str, str]) -> str:
    for value in secrets.values():
        if not value:
            continue
        for variant in secret_variants(value):
            text = text.replace(variant, REDACTED)
    return text


def read_bounded(path: Path, max_bytes: int) -> tuple[bytes, int, bool]:
    """Read at most ``max_bytes``, keeping the head and tail of larger files."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size <= max_bytes:
            return stream.read(), size, False
        half = max_bytes // 2
        head = stream.read(half)
        stream.seek(size - half)
        tail = stream.read()
    marker = TRUNCATION_MARKER.format(omitted=size - 2 * half).encode()
    return head + marker + tail, size, True


def run_artifact_prefix(run: Run) -> str:
    """Partition by workspace, then by the run's creation day (UTC).

    A workspace's artifacts share one prefix, so deleting a tenant is one
    prefix delete; within it, age-based sweeps can drop whole day folders. The
    path is derived from stored fields only, so DBOS retries rewrite the same
    object instead of creating a new one.
    """
    created = run.created_at.astimezone(UTC)
    return f"runs/{run.job.workspace_id}/{created:%Y/%m/%d}/{run.pk}"


def output_path(run: Run) -> str:
    return f"{run_artifact_prefix(run)}/output.log"


def input_path(run: Run) -> str:
    return f"{run_artifact_prefix(run)}/input.md"


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


def store_run_output(
    run: Run, raw_output: Path, secrets: dict[str, str]
) -> StoredOutput:
    content, size, truncated = read_bounded(raw_output, settings.RUN_OUTPUT_MAX_BYTES)
    text = redact(content.decode(errors="replace"), secrets).encode()
    storage = artifact_storage()
    path = output_path(run)
    storage.delete(path)
    saved = storage.save(path, ContentFile(text))
    return StoredOutput(
        path=saved,
        size_bytes=size,
        sha256=hashlib.sha256(text).hexdigest(),
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
