"""Publish changes to GitHub as a draft pull request.

Two kinds of change get published: a run's patch, and a new version of one
file, such as .secondshift.yaml with a job added.

This is the only code that asks GitHub for write access. Each token is
limited to one repository and to contents and pull requests, and is used for
one push and one request. It never reaches the sandbox or the model.

A run's commit is built in the control plane's mirror from the pinned commit
and the run's stored patch, so publishing again never needs the agent or its
sandbox. The author is the account that pushes: the App's bot, or the person
who owns the token. With the same author, message and date, a retry makes the
same commit and pushes the same branch again instead of making a new one.
"""

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from secondshift.connections.models import Connection
from secondshift.git import commands
from secondshift.git.commands import GitError
from secondshift.github import credentials, pulls
from secondshift.github.authors import GitAuthor, app_author, token_author
from secondshift.github.errors import GitHubError
from secondshift.github.git import authenticated_git
from secondshift.repositories.github.access import GitHubAccess, github_access
from secondshift.repositories.github.sync import fetch_github_commit
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


@dataclass(frozen=True)
class WriteAccess:
    """A write token for one repository, and the account it commits as."""

    access: GitHubAccess
    author: GitAuthor
    with_token: bool


def write_access(repository: Repository) -> WriteAccess:
    if not repository.is_github:
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
    return WriteAccess(access, author, with_token)


def push_branch(mirror: Path, write: WriteAccess, commit: str, branch: str) -> None:
    try:
        authenticated_git(
            mirror,
            write.access.token,
            "push",
            "--quiet",
            "--",
            write.access.clone_url,
            f"{commit}:refs/heads/{branch}",
        )
    except GitHubError as error:
        raise PublishError(
            f"GitHub refused the push to {branch}. "
            + (TOKEN_PUSH_HINT if write.with_token else APP_PUSH_HINT)
        ) from error


def publish_pull_request(
    repository: Repository, commit_sha: str, patch: Path, draft: DraftPullRequest
) -> PublishedPullRequest:
    write = write_access(repository)
    with locked_mirror(repository.pk) as mirror:
        if not has_commit(mirror, commit_sha):
            try:
                fetch_github_commit(write.access, commit_sha, mirror)
            except GitHubError as error:
                # These messages never contain the token.
                raise PublishError(str(error)) from error
        commit = commit_patch(mirror, commit_sha, patch, draft, write.author)
        push_branch(mirror, write, commit, draft.branch)
    return open_draft(write.access.full_name, write.access.token, draft)


def publish_file_change(
    repository: Repository,
    path: str,
    change: Callable[[str | None], str],
    draft: DraftPullRequest,
) -> PublishedPullRequest:
    """Open a draft that changes one file on the newest commit of draft.base.

    change gets the file's text, or None when the file is missing, and
    returns the new text. A branch an earlier attempt pushed is kept, and the
    draft opened from it is returned, so asking twice opens one pull request.
    """
    write = write_access(repository)
    token, clone_url = write.access.token, write.access.clone_url
    with locked_mirror(repository.pk) as mirror:
        try:
            pushed = authenticated_git(
                mirror, token, "ls-remote", "--heads", "--", clone_url, draft.branch
            )
            if not pushed:
                authenticated_git(
                    mirror,
                    token,
                    "fetch",
                    "--no-tags",
                    "--",
                    clone_url,
                    f"refs/heads/{draft.base}",
                )
                base = authenticated_git(mirror, token, "rev-parse", "FETCH_HEAD")
        except GitHubError as error:
            # These messages never contain the token.
            raise PublishError(str(error)) from error
        if not pushed:
            text = change(file_at(mirror, base, path))
            commit = commit_file(mirror, base, path, text, draft, write.author)
            push_branch(mirror, write, commit, draft.branch)
    return open_draft(write.access.full_name, token, draft)


def file_at(mirror: Path, commit_sha: str, path: str) -> str | None:
    """A file's text at a commit, or None when the commit has no such file."""
    try:
        found = commands.run(mirror, "cat-file", "blob", f"{commit_sha}:{path}")
    except GitError as error:
        raise PublishError("The repository could not be read.") from error
    return found.stdout.decode() if found.returncode == 0 else None


def commit_file(
    mirror: Path,
    commit_sha: str,
    path: str,
    text: str,
    draft: DraftPullRequest,
    author: GitAuthor,
) -> str:
    """Commit text as the new content of path, on top of commit_sha."""
    with tempfile.TemporaryDirectory(prefix="secondshift-publish-") as folder:
        content = Path(folder) / "content"
        content.write_text(text)
        index = str(Path(folder) / "index")
        blob = git(mirror, index, draft, author, "hash-object", "-w", str(content))
        git(mirror, index, draft, author, "read-tree", commit_sha)
        git(
            mirror,
            index,
            draft,
            author,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob},{path}",
        )
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
        "GIT_INDEX_FILE": index,
        "GIT_AUTHOR_NAME": author.name,
        "GIT_AUTHOR_EMAIL": author.email,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_NAME": author.name,
        "GIT_COMMITTER_EMAIL": author.email,
        "GIT_COMMITTER_DATE": when,
    }
    try:
        return commands.output(mirror, *args, extra_environment=environment)
    except GitError as error:
        raise PublishError("The commit could not be built.") from error


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
