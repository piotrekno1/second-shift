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
from typing import Any

import httpx

from secondshift.github.app import API_URL, installation_token
from secondshift.github.errors import GitHubError
from secondshift.github.git import authenticated_git
from secondshift.repositories.github_sync import (
    fetch_github_commit,
    github_repository_metadata,
)
from secondshift.repositories.mirrors import has_commit, locked_mirror
from secondshift.repositories.models import Repository

WRITE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}
# GitHub refuses a longer description.
MAX_BODY_CHARS = 60_000
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
                f"https://github.com/{full_name}.git",
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


def github_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def open_draft(
    full_name: str, token: str, draft: DraftPullRequest
) -> PublishedPullRequest:
    """Open the draft, or find the one an earlier attempt opened."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owner = full_name.split("/", 1)[0]
    with github_client() as client:
        try:
            created = client.post(
                f"{API_URL}/repos/{full_name}/pulls",
                headers=headers,
                json={
                    "title": draft.title,
                    "head": draft.branch,
                    "base": draft.base,
                    "body": draft.body[:MAX_BODY_CHARS],
                    "draft": True,
                },
            )
            if created.status_code == 201:
                return published(created.json())
            existing = client.get(
                f"{API_URL}/repos/{full_name}/pulls",
                headers=headers,
                params={"head": f"{owner}:{draft.branch}", "state": "all"},
            )
            found = existing.json() if existing.status_code == 200 else []
        except (httpx.HTTPError, ValueError) as error:
            raise PublishError("GitHub could not be reached.") from error
    if isinstance(found, list) and found:
        return published(found[0])
    raise PublishError(
        f"GitHub did not open the pull request: {github_message(created)}"
    )


def published(payload: Any) -> PublishedPullRequest:
    if not isinstance(payload, dict) or not isinstance(payload.get("html_url"), str):
        raise PublishError("GitHub returned an invalid pull request.")
    return PublishedPullRequest(number=int(payload["number"]), url=payload["html_url"])


def github_message(response: httpx.Response) -> str:
    """GitHub's own short reason, which never contains the token."""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    errors = payload.get("errors") if isinstance(payload, dict) else None
    details = [
        str(item.get("message"))
        for item in errors or []
        if isinstance(item, dict) and item.get("message")
    ]
    message = details[0] if details else str(payload.get("message", ""))
    return (message or f"HTTP {response.status_code}")[:200]
