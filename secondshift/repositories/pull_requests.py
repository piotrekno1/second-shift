"""Publish a run's changes to GitHub as a draft pull request.

This is the only code that asks GitHub for write access. Each token is
limited to one repository and to contents and pull requests, and is used for
one push and one request. It never reaches the sandbox or the model.

The commit is built in the control plane's mirror from the pinned commit and
the run's stored patch, so publishing again never needs the agent or its
sandbox. A fixed author, message and date give the same commit every time, so
a retry pushes the same branch again instead of making a new one.
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from secondshift.github import pulls
from secondshift.github.app import installation_token
from secondshift.github.errors import GitHubError
from secondshift.github.git import authenticated_git
from secondshift.github.identity import clone_url
from secondshift.repositories.github_sync import (
    fetch_github_commit,
    github_repository_metadata,
)
from secondshift.repositories.mirrors import has_commit, locked_mirror
from secondshift.repositories.models import Repository

WRITE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}
MAX_BODY_CHARS = pulls.MAX_BODY_CHARS
AUTHOR_NAME = "SecondShift"
AUTHOR_EMAIL = "secondshift@users.noreply.github.com"


class PublishError(Exception):
    """A publishing step failed. The message is safe to show on the run."""


@dataclass(frozen=True)
class DraftPullRequest:
    branch: str
    base: str
    title: str
    body: str
    message: str
    committed_at: datetime


@dataclass(frozen=True)
class PublishedPullRequest:
    number: int
    url: str


def publish_pull_request(
    repository: Repository, commit_sha: str, patch: Path, draft: DraftPullRequest
) -> PublishedPullRequest:
    if repository.source_type != Repository.SourceType.GITHUB:
        raise PublishError("Only GitHub repositories can receive pull requests.")
    try:
        full_name = github_repository_metadata(repository)["full_name"]
        token = installation_token(
            repository.github_connection.external_account_id,
            int(repository.external_id),
            permissions=WRITE_PERMISSIONS,
        )
    except GitHubError as error:
        raise PublishError(
            "GitHub refused write access. Give the GitHub App Contents and Pull "
            "requests write permission, then retry."
        ) from error
    with locked_mirror(repository.pk) as mirror:
        if not has_commit(mirror, commit_sha):
            try:
                fetch_github_commit(repository, commit_sha, mirror)
            except GitHubError as error:
                # These messages never contain the token.
                raise PublishError(str(error)) from error
        commit = commit_patch(mirror, commit_sha, patch, draft)
        try:
            authenticated_git(
                mirror,
                token,
                "push",
                "--quiet",
                "--",
                clone_url(full_name),
                f"{commit}:refs/heads/{draft.branch}",
            )
        except GitHubError as error:
            raise PublishError(
                f"GitHub refused the push to {draft.branch}. Changes to workflow "
                "files are refused, and so is a branch someone else changed."
            ) from error
    return open_draft(full_name, token, draft)


def commit_patch(
    mirror: Path, commit_sha: str, patch: Path, draft: DraftPullRequest
) -> str:
    """Apply the patch to the pinned commit in a scratch index, and commit it."""
    with tempfile.TemporaryDirectory(prefix="secondshift-publish-") as folder:
        index = str(Path(folder) / "index")
        git(mirror, index, draft, "read-tree", commit_sha)
        try:
            git(mirror, index, draft, "apply", "--cached", "--binary", str(patch))
        except PublishError as error:
            raise PublishError(
                "The changes no longer apply to the run's commit."
            ) from error
        tree = git(mirror, index, draft, "write-tree")
        return git(
            mirror,
            index,
            draft,
            "commit-tree",
            tree,
            "-p",
            commit_sha,
            "-m",
            draft.message,
        )


def git(mirror: Path, index: str, draft: DraftPullRequest, *args: str) -> str:
    when = draft.committed_at.isoformat()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(mirror),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_INDEX_FILE": index,
        "GIT_AUTHOR_NAME": AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_NAME": AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
        "GIT_COMMITTER_DATE": when,
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(mirror), "-c", "core.hooksPath=/dev/null", *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublishError("The commit could not be built.") from error
    if result.returncode:
        raise PublishError("The commit could not be built.")
    return result.stdout.strip()


def open_draft(
    full_name: str, token: str, draft: DraftPullRequest
) -> PublishedPullRequest:
    try:
        opened = pulls.open_draft(
            token, full_name, draft.title, draft.branch, draft.base, draft.body
        )
    except GitHubError as error:
        # These messages never contain the token.
        raise PublishError(str(error)) from error
    return PublishedPullRequest(number=opened["number"], url=opened["html_url"])
