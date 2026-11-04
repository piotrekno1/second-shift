"""Resolve a submitted skill at a committed revision without syncing jobs."""

import re
from pathlib import Path

from django.conf import settings

from secondshift.config.constants import COMMIT_SHA_PATTERN
from secondshift.github.git import authenticated_git
from secondshift.repositories.github_access import github_access
from secondshift.repositories.local import committed_file, git
from secondshift.repositories.mirrors import locked_mirror
from secondshift.repositories.models import Repository


class SkillSourceError(ValueError):
    pass


def skill_commit(
    repository: Repository, skill_path: str | None, revision: str | None
) -> str:
    """Use the followed branch by default; explicit branches and full SHAs work."""
    if (
        repository.source_type == Repository.SourceType.LOCAL
        and not settings.LOCAL_REPOSITORIES_ENABLED
    ):
        raise SkillSourceError("Local repositories are not enabled on this server.")
    requested = revision or repository.default_branch
    is_commit = bool(re.fullmatch(COMMIT_SHA_PATTERN, requested))
    if not is_commit:
        # Only branch names are accepted, never arbitrary Git revision expressions.
        git(
            Path(repository.local_path) if repository.local_path else Path.cwd(),
            "check-ref-format",
            f"refs/heads/{requested}",
        )
    if repository.source_type == Repository.SourceType.LOCAL:
        root = Path(repository.local_path)
        ref = requested if is_commit else f"refs/heads/{requested}"
        commit = git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
        if skill_path is not None:
            committed_file(root, commit, skill_path)
        return commit
    access = github_access(repository)
    token = access.token
    with locked_mirror(repository.pk) as mirror:
        authenticated_git(
            mirror,
            token,
            "fetch",
            "--no-tags",
            "--",
            access.clone_url,
            requested if is_commit else f"refs/heads/{requested}",
        )
        commit = authenticated_git(
            mirror, token, "rev-parse", "--verify", "FETCH_HEAD^{commit}"
        )
        if not re.fullmatch(COMMIT_SHA_PATTERN, commit):
            raise SkillSourceError("The repository did not resolve to a full commit.")
        if skill_path is not None:
            committed_file(mirror, commit, skill_path)
        return commit
