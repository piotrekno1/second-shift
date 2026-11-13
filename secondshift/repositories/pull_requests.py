"""Publish a run's changes to GitHub as a draft pull request.

This is the only code that asks GitHub for write access. Each token is
limited to one repository and to contents and pull requests, and is used for
one push and one request. It never reaches the sandbox or the model.

The commit is built in the control plane's mirror from the pinned commit and
the run's stored patch, so publishing again never needs the agent or its
sandbox. The author is the account that pushes: the App's bot, or the person
who owns the token. With the same author, message and date, a retry makes the
same commit and pushes the same branch again instead of making a new one.
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from secondshift.connections.models import Connection
from secondshift.github import credentials, pulls
from secondshift.github.authors import GitAuthor, app_author, token_author
from secondshift.github.errors import GitHubError
from secondshift.github.git import authenticated_git
from secondshift.repositories.github_access import github_access
from secondshift.repositories.github_sync import fetch_github_commit
from secondshift.repositories.mirrors import has_commit, locked_mirror
from secondshift.repositories.models import Repository

APP_PUSH_HINT = (
    "Changes to workflow files are refused, and so is a branch someone else changed."
)
TOKEN_PUSH_HINT = (
    "Give the token Contents and Pull requests write access. A branch someone "
    "else changed is refused too, and so are workflow files unless the token "
    "has Workflows write access."
)


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
    with_token = (
        repository.github_connection is not None
        and repository.github_connection.auth_strategy
        == Connection.AuthStrategy.API_KEY
    )
    try:
        # One token for the fetch, the push and the pull request.
        access = github_access(repository, credentials.WRITE)
    except GitHubError as error:
        if with_token:
            # Token errors already say what to fix, such as an expired token.
            raise PublishError(str(error)) from error
        raise PublishError(
            "GitHub refused write access. Give the GitHub App Contents and Pull "
            "requests write permission, then retry."
        ) from error
    try:
        author = (token_author if with_token else app_author)(access.token)
    except GitHubError as error:
        raise PublishError(
            "GitHub did not say which account publishes the changes."
        ) from error
    with locked_mirror(repository.pk) as mirror:
        if not has_commit(mirror, commit_sha):
            try:
                fetch_github_commit(access, commit_sha, mirror)
            except GitHubError as error:
                # These messages never contain the token.
                raise PublishError(str(error)) from error
        commit = commit_patch(mirror, commit_sha, patch, draft, author)
        try:
            authenticated_git(
                mirror,
                access.token,
                "push",
                "--quiet",
                "--",
                access.clone_url,
                f"{commit}:refs/heads/{draft.branch}",
            )
        except GitHubError as error:
            raise PublishError(
                f"GitHub refused the push to {draft.branch}. "
                + (TOKEN_PUSH_HINT if with_token else APP_PUSH_HINT)
            ) from error
    return open_draft(access.full_name, access.token, draft)


def commit_patch(
    mirror: Path,
    commit_sha: str,
    patch: Path,
    draft: DraftPullRequest,
    author: GitAuthor,
) -> str:
    """Apply the patch to the pinned commit in a scratch index, and commit it."""
    with tempfile.TemporaryDirectory(prefix="secondshift-publish-") as folder:
        index = str(Path(folder) / "index")
        git(mirror, index, draft, author, "read-tree", commit_sha)
        try:
            git(
                mirror,
                index,
                draft,
                author,
                "apply",
                "--cached",
                "--binary",
                str(patch),
            )
        except PublishError as error:
            raise PublishError(
                "The changes no longer apply to the run's commit."
            ) from error
        tree = git(mirror, index, draft, author, "write-tree")
        return git(
            mirror,
            index,
            draft,
            author,
            "commit-tree",
            tree,
            "-p",
            commit_sha,
            "-m",
            draft.message,
        )


def git(
    mirror: Path, index: str, draft: DraftPullRequest, author: GitAuthor, *args: str
) -> str:
    when = draft.committed_at.isoformat()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(mirror),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_INDEX_FILE": index,
        "GIT_AUTHOR_NAME": author.name,
        "GIT_AUTHOR_EMAIL": author.email,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_NAME": author.name,
        "GIT_COMMITTER_EMAIL": author.email,
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
