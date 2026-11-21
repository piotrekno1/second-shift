"""Add a job to a .secondshift.yaml without rewriting the rest of the file.

The new job is inserted as text at the end of the top-level jobs mapping, so
comments, order and quoting elsewhere stay as the author wrote them. The
result is parsed again and must equal the old file plus the new job. A layout
this can't edit safely, such as jobs written in flow style, is refused with a
message saying to add the job by hand.
"""

import re
from typing import Any

import yaml

from secondshift.jobs.manifest import ManifestError, parse_manifest
from secondshift.repositories.models import MANIFEST_FILE

JOBS_LINE = re.compile(r"^jobs:[ \t]*(#.*)?$")
DEFAULT_INDENT = 2
CANNOT_EDIT = (
    f"SecondShift can't add the job to this {MANIFEST_FILE} without rewriting "
    "it. Copy the job and add it by hand."
)
KEY_CHARS = 60


def job_key(name: str) -> str:
    """Lowercase words joined by hyphens. The dashboard shows the same key."""
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:KEY_CHARS]
    return key.strip("-") or "skill"


class IndentedDumper(yaml.SafeDumper):
    """Indents list items under their key, as people write them."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def job_block(key: str, job: dict[str, Any], indent: int) -> str:
    text = yaml.dump(
        {key: job},
        Dumper=IndentedDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        indent=indent,
        width=1000,
    )
    return "".join(" " * indent + line for line in text.splitlines(keepends=True))


def parsed(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ManifestError(f"Invalid {MANIFEST_FILE}: {error}") from error


def checked(text: str) -> None:
    try:
        parse_manifest(text)
    except (ValueError, yaml.YAMLError) as error:
        raise ManifestError(f"Invalid {MANIFEST_FILE}: {error}") from error


def add_job(text: str | None, key: str, job: dict[str, Any]) -> str:
    """The file with the job added, or a new file holding only that job.

    Raises ManifestError when the file is invalid, already has a job called
    key, or can't be edited without rewriting it.
    """
    if text is None or not text.strip():
        created = f"version: 1\njobs:\n{job_block(key, job, DEFAULT_INDENT)}"
        checked(created)
        return created
    checked(text)
    before = parsed(text)
    if key in before["jobs"]:
        raise ManifestError(
            f"{MANIFEST_FILE} already has a job called {key}. Choose another name."
        )
    lines = text.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if JOBS_LINE.match(line.rstrip())),
        None,
    )
    if start is None:
        raise ManifestError(CANNOT_EDIT)
    # The job goes after the last indented line under jobs:, before any
    # comment or blank lines that lead into the next top-level key.
    end, indent = start + 1, None
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            break
        if indent is None:
            indent = len(line) - len(line.lstrip(" "))
        end = index + 1
    head = "".join(lines[:end])
    if not head.endswith("\n"):
        head += "\n"
    edited = head + job_block(key, job, indent or DEFAULT_INDENT) + "".join(lines[end:])
    expected = {**before, "jobs": {**before["jobs"], key: job}}
    if parsed(edited) != expected:
        raise ManifestError(CANNOT_EDIT)
    checked(edited)
    return edited
