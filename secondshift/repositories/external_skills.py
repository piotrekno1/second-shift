"""Skills from another public GitHub repository, pinned to one commit.

A skill is a SKILL.md and the files in its folder. SecondShift fetches them
without credentials, so only public repositories work. A run gets the folder
at the pinned commit, mounted read-only at /skill. Nothing in the skill's
repository can change how the sandbox runs or what it may reach.
"""

import fcntl
import hashlib
import os
import posixpath
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings

from secondshift.config.constants import COMMIT_SHA_PATTERN, GITHUB_REPOSITORY_PATTERN
from secondshift.github.errors import GitHubError
from secondshift.github.git import public_git
from secondshift.repositories.local import LocalRepositoryError, committed_file
from secondshift.repositories.mirrors import has_commit
from secondshift.repositories.snapshots import (
    SKILL_LIMITS,
    SnapshotError,
    tree_entries,
)
from secondshift.repositories.sources import (
    SourceSnapshot,
    file_sha256,
    skill_mirrors_root,
    skill_snapshots_root,
    write_atomically,
)

SKILL_FILE = "SKILL.md"
# Branch and tag names a person may give. Git checks them again.
REF_NAME = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}")


class ExternalSkillError(ValueError):
    """Worded for the person who named the skill."""


@dataclass(frozen=True)
class ExternalSkill:
    repository: str
    path: str
    commit_sha: str
    text: str
    file_count: int
    size_bytes: int


def check_reference(repository: str, path: str) -> None:
    if not re.fullmatch(GITHUB_REPOSITORY_PATTERN, repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        raise ExternalSkillError("Name the repository as owner/name.")
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or "\0" in path
        or posixpath.normpath(path) != path
    ):
        raise ExternalSkillError("The skill path must be a path inside its repository.")
    if pure.name != SKILL_FILE:
        raise ExternalSkillError(f"The skill path must point to a {SKILL_FILE} file.")


def check_ref(ref: str) -> None:
    if re.fullmatch(COMMIT_SHA_PATTERN, ref):
        return
    if (
        not REF_NAME.fullmatch(ref)
        or ".." in ref
        or "//" in ref
        or ref.endswith(("/", ".lock", "."))
        or "@{" in ref
    ):
        raise ExternalSkillError("Give a branch, a tag or a full commit SHA.")
    completed = subprocess.run(
        ["git", "check-ref-format", f"refs/heads/{ref}"],
        capture_output=True,
        check=False,
        timeout=10,
    )
    if completed.returncode:
        raise ExternalSkillError("Give a branch, a tag or a full commit SHA.")


def skill_folder(path: str) -> str:
    """The folder whose files a run gets. Empty for a SKILL.md at the root."""
    return posixpath.dirname(path)


def cache_key(repository: str) -> str:
    # Repository names are case-insensitive on GitHub.
    return hashlib.sha256(repository.lower().encode()).hexdigest()[:32]


@contextmanager
def locked_skill_mirror(repository: str) -> Iterator[Path]:
    """A bare mirror of the skill's repository, held under a lock."""
    key = cache_key(repository)
    path = skill_mirrors_root() / f"{key}.git"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    locks = settings.SOURCE_CACHE_ROOT / "locks"
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (locks / f"skill-{key}.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            subprocess.run(
                ["git", "init", "--quiet", "--bare", str(path)],
                check=True,
                capture_output=True,
                timeout=30,
            )
        # Mark it used, so cache cleanup keeps it.
        os.utime(path)
        yield path


def clone_url(repository: str) -> str:
    return f"{settings.EXTERNAL_SKILLS_BASE_URL}/{repository}.git"


def fetch(mirror: Path, repository: str, ref: str) -> str:
    """Fetch one branch, tag or commit and return its full commit SHA."""
    try:
        public_git(
            mirror, "fetch", "--no-tags", "--depth=1", "--", clone_url(repository), ref
        )
        commit = public_git(mirror, "rev-parse", "--verify", "FETCH_HEAD^{commit}")
    except GitHubError as error:
        raise ExternalSkillError(
            f"Could not fetch {ref} from {repository}. Only public GitHub "
            "repositories work, and the branch, tag or commit must exist."
        ) from error
    if not re.fullmatch(COMMIT_SHA_PATTERN, commit):
        raise ExternalSkillError(f"{repository} did not resolve to a full commit.")
    # Keep the commit's objects in the mirror until cache cleanup.
    public_git(mirror, "update-ref", f"refs/secondshift/skills/{commit}", commit)
    return commit


def mirrored_commit(mirror: Path, repository: str, ref: str) -> str:
    if re.fullmatch(COMMIT_SHA_PATTERN, ref) and has_commit(mirror, ref):
        return ref
    commit = fetch(mirror, repository, ref)
    if re.fullmatch(COMMIT_SHA_PATTERN, ref) and commit != ref:
        raise ExternalSkillError(f"{repository} did not return commit {ref[:12]}.")
    return commit


def require_enabled() -> None:
    if not settings.EXTERNAL_SKILLS_ENABLED:
        raise ExternalSkillError(
            "This server does not run skills from other repositories."
        )


def resolve_external_skill(
    repository: str, path: str, ref: str | None = None
) -> ExternalSkill:
    """Fetch the skill at a branch, tag or commit, and pin it to a commit.

    Without a ref, the repository's default branch is used.
    """
    require_enabled()
    check_reference(repository, path)
    requested = ref or "HEAD"
    if ref is not None:
        check_ref(ref)
    with locked_skill_mirror(repository) as mirror:
        commit = mirrored_commit(mirror, repository, requested)
        try:
            text = committed_file(mirror, commit, path)
        except LocalRepositoryError as error:
            raise ExternalSkillError(
                f"{path} is not a file in {repository} at {commit[:12]}."
            ) from error
        try:
            entries = tree_entries(mirror, commit, skill_folder(path))
        except SnapshotError as error:
            raise ExternalSkillError(str(error)) from error
    return ExternalSkill(
        repository=repository,
        path=path,
        commit_sha=commit,
        text=text,
        file_count=len(entries),
        size_bytes=sum(entry.size for entry in entries),
    )


def skill_snapshot(repository: str, path: str, commit_sha: str) -> SourceSnapshot:
    """The skill folder at a commit, as a snapshot a run can unpack.

    Built and cached if needed. Raises SnapshotError or ExternalSkillError.
    """
    require_enabled()
    check_reference(repository, path)
    if not re.fullmatch(COMMIT_SHA_PATTERN, commit_sha):
        raise SnapshotError("A full commit SHA is required.")
    name = hashlib.sha256(f"{repository.lower()}\0{path}".encode()).hexdigest()[:32]
    target = skill_snapshots_root() / f"{commit_sha}-{name}.tar.gz"
    if target.exists():
        target.touch()
    else:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with locked_skill_mirror(repository) as mirror:
            mirrored_commit(mirror, repository, commit_sha)
            write_atomically(
                mirror, commit_sha, target, skill_folder(path), SKILL_LIMITS
            )
    return SourceSnapshot(
        path=target, sha256=file_sha256(target), size_bytes=target.stat().st_size
    )
