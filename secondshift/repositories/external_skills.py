"""Skills from another GitHub repository, pinned to one commit.

A skill is a SKILL.md and the files in its folder. A run gets the folder at
the pinned commit, mounted read-only at /skill. Nothing in the skill's
repository can change how the sandbox runs or what it may reach.

A public repository is fetched without credentials, and its cache is shared.
A private one is fetched through a GitHub connection of the workspace that
names it. Its cache belongs to that workspace, and access is checked again
each time, so a lost connection stops it even when the files are cached.
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
from secondshift.connections.dao import connected_github_sources
from secondshift.connections.github import can_check, connection_source
from secondshift.github import credentials, repos
from secondshift.github.errors import GitHubError
from secondshift.github.git import authenticated_git, public_git
from secondshift.repositories.github_access import connection_access
from secondshift.repositories.local import LocalRepositoryError, committed_file
from secondshift.repositories.mirrors import has_commit
from secondshift.repositories.snapshots import (
    SKILL_LIMITS,
    SnapshotError,
    check_limits,
    folder_digest,
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
    # The skill folder's files, as `sha256:<hex>`. See folder_digest.
    digest: str


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


@dataclass(frozen=True)
class SkillReader:
    """How one workspace reads one skill repository. Never stored or logged.

    A public repository has no token and no workspace. A private one has the
    token of the connection that reads it, and its cache is the workspace's.
    """

    repository: str
    workspace_id: int | None = None
    token: str | None = None

    def git(self, mirror: Path, *arguments: str) -> str:
        if self.token is None:
            return public_git(mirror, *arguments)
        return authenticated_git(mirror, self.token, *arguments)


def skill_reader(workspace_id: int, repository: str) -> SkillReader:
    """A connection that reads the repository when it is private.

    GitHub decides whether it is private, never the cache. Connections that
    cannot see the repository are skipped. Without one, it is read as public.
    """
    for connection in connected_github_sources(workspace_id):
        if not can_check(connection):
            continue
        source = connection_source(connection)
        try:
            token = credentials.token_for(source, None, credentials.METADATA_READ)
            metadata = repos.repository_by_name(token, repository)
        except GitHubError:
            continue
        if not metadata.get("private"):
            return SkillReader(repository)
        if metadata["full_name"].lower() != repository.lower():
            raise ExternalSkillError(
                f"{repository} is now {metadata['full_name']}. Use its new name."
            )
        try:
            access = connection_access(connection, metadata["id"])
        except GitHubError:
            continue
        return SkillReader(repository, workspace_id, access.token)
    return SkillReader(repository)


def skill_folder(path: str) -> str:
    """The folder whose files a run gets. Empty for a SKILL.md at the root."""
    return posixpath.dirname(path)


def cache_key(reader: SkillReader, *parts: str) -> str:
    """One name per repository, and per workspace for a private one."""
    # Repository names are case-insensitive on GitHub.
    scope = "public" if reader.workspace_id is None else str(reader.workspace_id)
    name = "\0".join([scope, reader.repository.lower(), *parts])
    return hashlib.sha256(name.encode()).hexdigest()[:32]


@contextmanager
def locked_skill_mirror(reader: SkillReader) -> Iterator[Path]:
    """A bare mirror of the skill's repository, held under a lock."""
    key = cache_key(reader)
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


def fetch(mirror: Path, reader: SkillReader, ref: str) -> str:
    """Fetch one branch, tag or commit and return its full commit SHA."""
    repository = reader.repository
    try:
        reader.git(
            mirror, "fetch", "--no-tags", "--depth=1", "--", clone_url(repository), ref
        )
        commit = public_git(mirror, "rev-parse", "--verify", "FETCH_HEAD^{commit}")
    except GitHubError as error:
        raise ExternalSkillError(
            f"Could not fetch {ref} from {repository}. The repository must be "
            "public, or readable by a GitHub connection in this workspace. "
            "The branch, tag or commit must exist."
        ) from error
    if not re.fullmatch(COMMIT_SHA_PATTERN, commit):
        raise ExternalSkillError(f"{repository} did not resolve to a full commit.")
    # Keep the commit's objects in the mirror until cache cleanup.
    public_git(mirror, "update-ref", f"refs/secondshift/skills/{commit}", commit)
    return commit


def mirrored_commit(mirror: Path, reader: SkillReader, ref: str) -> str:
    if re.fullmatch(COMMIT_SHA_PATTERN, ref) and has_commit(mirror, ref):
        return ref
    commit = fetch(mirror, reader, ref)
    if re.fullmatch(COMMIT_SHA_PATTERN, ref) and commit != ref:
        raise ExternalSkillError(
            f"{reader.repository} did not return commit {ref[:12]}."
        )
    return commit


def require_enabled() -> None:
    if not settings.EXTERNAL_SKILLS_ENABLED:
        raise ExternalSkillError(
            "This server does not run skills from other repositories."
        )


def resolve_external_skill(
    workspace_id: int,
    repository: str,
    path: str,
    ref: str | None = None,
    digest: str | None = None,
) -> ExternalSkill:
    """Fetch the skill at a branch, tag or commit, and pin it to a commit.

    Without a ref, the repository's default branch is used. With a digest,
    the skill folder's files must match it.
    """
    require_enabled()
    check_reference(repository, path)
    requested = ref or "HEAD"
    if ref is not None:
        check_ref(ref)
    reader = skill_reader(workspace_id, repository)
    with locked_skill_mirror(reader) as mirror:
        commit = mirrored_commit(mirror, reader, requested)
        try:
            text = committed_file(mirror, commit, path)
        except LocalRepositoryError as error:
            raise ExternalSkillError(
                f"{path} is not a file in {repository} at {commit[:12]}."
            ) from error
        try:
            entries = tree_entries(mirror, commit, skill_folder(path))
            check_limits(entries, SKILL_LIMITS)
            found = folder_digest(mirror, entries)
        except SnapshotError as error:
            raise ExternalSkillError(str(error)) from error
    if digest is not None and found != digest:
        raise ExternalSkillError(
            f"The skill folder of {path} in {repository} at {commit[:12]} has "
            f"digest {found}, not {digest}."
        )
    return ExternalSkill(
        repository=repository,
        path=path,
        commit_sha=commit,
        text=text,
        file_count=len(entries),
        size_bytes=sum(entry.size for entry in entries),
        digest=found,
    )


def skill_snapshot(
    workspace_id: int, repository: str, path: str, commit_sha: str
) -> SourceSnapshot:
    """The skill folder at a commit, as a snapshot a run can unpack.

    Built and cached if needed. Raises SnapshotError or ExternalSkillError.
    """
    require_enabled()
    check_reference(repository, path)
    if not re.fullmatch(COMMIT_SHA_PATTERN, commit_sha):
        raise SnapshotError("A full commit SHA is required.")
    # Checked before the cache, so lost access stops a private skill.
    reader = skill_reader(workspace_id, repository)
    target = skill_snapshots_root() / f"{commit_sha}-{cache_key(reader, path)}.tar.gz"
    if target.exists():
        target.touch()
    else:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with locked_skill_mirror(reader) as mirror:
            mirrored_commit(mirror, reader, commit_sha)
            write_atomically(
                mirror, commit_sha, target, skill_folder(path), SKILL_LIMITS
            )
    return SourceSnapshot(
        path=target, sha256=file_sha256(target), size_bytes=target.stat().st_size
    )
