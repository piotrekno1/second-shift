from typing import Any

from secondshift.config.constants import MAX_CHANGES_BYTES


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def collection_summary(outputs: dict[str, Any]) -> str:
    """One timeline line about what the sandbox left behind, or none."""
    changes = outputs.get("changes") or {}
    parts: list[str] = []
    if changes.get("path"):
        parts.append(f"changes to {plural(changes['file_count'], 'file')}")
    if files := outputs.get("files"):
        parts.append(plural(len(files), "saved file"))
    lines = [f"Collected {' and '.join(parts)}."] if parts else []
    if changes.get("too_large"):
        limit = MAX_CHANGES_BYTES // (1024 * 1024)
        lines.append(
            f"Changes to {plural(changes['file_count'], 'file')} were larger than "
            f"{limit} MiB, so they were not kept."
        )
    if skipped := outputs.get("skipped"):
        lines.append(f"Skipped {plural(len(skipped), 'saved file')}.")
    return " ".join(lines)
