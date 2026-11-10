from secondshift.config.constants import MAX_CHANGES_BYTES
from secondshift.runs.outputs import RunOutputs


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def collection_summary(outputs: RunOutputs) -> str:
    """One timeline line about what the sandbox left behind, or none."""
    changes = outputs.changes
    parts: list[str] = []
    if changes.path:
        parts.append(f"changes to {plural(changes.file_count, 'file')}")
    if outputs.files:
        parts.append(plural(len(outputs.files), "saved file"))
    lines = [f"Collected {' and '.join(parts)}."] if parts else []
    if changes.too_large:
        limit = MAX_CHANGES_BYTES // (1024 * 1024)
        lines.append(
            f"Changes to {plural(changes.file_count, 'file')} were larger than "
            f"{limit} MiB, so they were not kept."
        )
    if outputs.skipped:
        lines.append(f"Skipped {plural(len(outputs.skipped), 'saved file')}.")
    return " ".join(lines)
