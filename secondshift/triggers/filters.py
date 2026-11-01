"""Read a webhook body against the job's webhook settings.

The settings name fields in the sender's own format, such as Grafana's
"status" or "groupKey". Nothing here knows any sender.
"""

import hashlib
import json
from typing import Any

from secondshift.jobs.manifest import duration_seconds

# Used when a job sets repeat_key without repeat_window.
REPEAT_WINDOW_DEFAULT = "1h"
MISSING = object()


def parse_body(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return None


def field_value(data: Any, path: str) -> Any:
    """The value at a dotted path, or MISSING."""
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return MISSING
        data = data[part]
    return data


def skip_reason(data: Any, skip_when: dict[str, Any]) -> str | None:
    """Why the body matches skip_when, or None. Every field must match."""
    if not skip_when:
        return None
    if all(field_value(data, path) == value for path, value in skip_when.items()):
        matched = ", ".join(f"{path} is {value}" for path, value in skip_when.items())
        return f"Skipped by the job's settings: {matched}."
    return None


def repeat_key(data: Any, path: str | None) -> str | None:
    """A hash of the repeat_key field's value. None when it is not set or absent."""
    if not path:
        return None
    value = field_value(data, path)
    if value is MISSING or value is None:
        return None
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{path}={canonical}".encode()).hexdigest()


def repeat_window_seconds(settings: dict[str, Any]) -> int:
    return duration_seconds(settings.get("repeat_window") or REPEAT_WINDOW_DEFAULT)
