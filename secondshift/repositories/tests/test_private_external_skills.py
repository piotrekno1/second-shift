"""Skills from private repositories, read through a workspace's GitHub connection."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from secondshift.connections.dao import save_github_source
from secondshift.connections.models import Connection
from secondshift.github import repos
from secondshift.github.errors import GitHubError
from secondshift.github.git import public_git
from secondshift.repositories import external_skills
from secondshift.repositories.external_skills import (
    ExternalSkillError,
    resolve_external_skill,
    skill_snapshot,
)
from secondshift.repositories.tests.test_external_skills import (
    PATH,
    REPOSITORY,
)
from secondshift.repositories.tests.test_external_skills import (
    vendor as vendor_fixture,
)
from secondshift.repositories.tests.test_local import run_git
from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace

pytestmark = pytest.mark.django_db
vendor = vendor_fixture

PRIVATE = "acme/private-skills"
TOKEN = "github_pat_" + "a" * 40


def head(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def connect(workspace: Workspace, token: str = TOKEN) -> Connection:
    return save_github_source(
        workspace,
        f"token:{workspace.slug}",
        {
            "kind": Connection.Kind.SOURCE,
            "auth_strategy": Connection.AuthStrategy.API_KEY,
            "display_name": "GitHub · owner",
            "access_token": token,
            "status": Connection.Status.CONNECTED,
        },
    )


def other_workspace() -> Workspace:
    user = get_user_model().objects.create_user(username="other")
    return create_workspace(name="Other", slug="other", owner=user)


class FakeGitHub:
    """GitHub's API and git server as one token sees them."""

    def __init__(self) -> None:
        # Which repositories a token can see, and what GitHub calls them.
        self.visible: dict[str, dict[str, str]] = {TOKEN: {PRIVATE: PRIVATE}}
        self.fetch_tokens: list[str] = []

    def metadata(self, token: str, name: str) -> dict[str, Any]:
        full_name = self.visible.get(token, {}).get(name)
        if full_name is None:
            raise GitHubError("Not found.")
        return {"id": 7, "full_name": full_name, "private": name == PRIVATE}

    def repository_by_name(self, token: str, full_name: str) -> dict[str, Any]:
        return self.metadata(token, full_name)

    def repository(self, token: str, repository_id: int) -> dict[str, Any]:
        return self.metadata(token, PRIVATE)

    def public_git(self, root: Path, *arguments: str) -> str:
        if arguments[0] == "fetch" and any(PRIVATE in item for item in arguments):
            raise GitHubError("Could not fetch the GitHub repository and branch.")
        return public_git(root, *arguments)

    def authenticated_git(self, root: Path, token: str, *arguments: str) -> str:
        self.fetch_tokens.append(token)
        return public_git(root, *arguments)


@pytest.fixture
def github(vendor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    """The vendor skills also served as a private repository."""
    served = tmp_path / "github" / f"{PRIVATE}.git"
    served.parent.mkdir(parents=True)
    run_git(tmp_path, "clone", "--bare", "--quiet", str(vendor), str(served))
    run_git(served, "config", "uploadpack.allowAnySHA1InWant", "true")
    fake = FakeGitHub()
    monkeypatch.setattr(repos, "repository_by_name", fake.repository_by_name)
    monkeypatch.setattr(repos, "repository", fake.repository)
    monkeypatch.setattr(external_skills, "public_git", fake.public_git)
    monkeypatch.setattr(external_skills, "authenticated_git", fake.authenticated_git)
    return fake


def test_a_private_skill_is_read_through_the_connection(
    workspace: Workspace, vendor: Path, github: FakeGitHub
) -> None:
    connect(workspace)

    skill = resolve_external_skill(workspace.pk, PRIVATE, PATH)
    snapshot = skill_snapshot(workspace.pk, PRIVATE, PATH, skill.commit_sha)

    assert skill.commit_sha == head(vendor)
    assert skill.text.startswith("---\nname: triage")
    assert snapshot.size_bytes > 0
    assert github.fetch_tokens == [TOKEN]


def test_a_private_skill_needs_a_connection_that_reads_it(
    workspace: Workspace, github: FakeGitHub
) -> None:
    with pytest.raises(ExternalSkillError, match="readable by a GitHub connection"):
        resolve_external_skill(workspace.pk, PRIVATE, PATH)

    connect(workspace, "github_pat_" + "b" * 40)
    with pytest.raises(ExternalSkillError, match="readable by a GitHub connection"):
        resolve_external_skill(workspace.pk, PRIVATE, PATH)
    assert github.fetch_tokens == []


def test_another_workspace_cannot_use_the_cached_skill(
    workspace: Workspace, github: FakeGitHub
) -> None:
    connect(workspace)
    commit = resolve_external_skill(workspace.pk, PRIVATE, PATH).commit_sha
    skill_snapshot(workspace.pk, PRIVATE, PATH, commit)
    other = other_workspace()

    with pytest.raises(ExternalSkillError, match="readable by a GitHub connection"):
        resolve_external_skill(other.pk, PRIVATE, PATH, commit)
    with pytest.raises(ExternalSkillError, match="readable by a GitHub connection"):
        skill_snapshot(other.pk, PRIVATE, PATH, commit)


def test_a_lost_connection_stops_a_cached_skill(
    workspace: Workspace, github: FakeGitHub
) -> None:
    connection = connect(workspace)
    commit = resolve_external_skill(workspace.pk, PRIVATE, PATH).commit_sha
    skill_snapshot(workspace.pk, PRIVATE, PATH, commit)

    connection.status = Connection.Status.REVOKED
    connection.save(update_fields=["status"])

    with pytest.raises(ExternalSkillError, match="readable by a GitHub connection"):
        skill_snapshot(workspace.pk, PRIVATE, PATH, commit)
    with pytest.raises(ExternalSkillError, match="readable by a GitHub connection"):
        resolve_external_skill(workspace.pk, PRIVATE, PATH, commit)


def test_a_renamed_private_repository_must_be_named_again(
    workspace: Workspace, github: FakeGitHub
) -> None:
    connect(workspace)
    github.visible[TOKEN] = {PRIVATE: "acme/renamed-skills"}

    with pytest.raises(ExternalSkillError, match="is now acme/renamed-skills"):
        resolve_external_skill(workspace.pk, PRIVATE, PATH)


def test_a_public_skill_is_fetched_without_the_token(
    workspace: Workspace, github: FakeGitHub
) -> None:
    connect(workspace)
    github.visible[TOKEN][REPOSITORY] = REPOSITORY
    other = other_workspace()

    commit = resolve_external_skill(workspace.pk, REPOSITORY, PATH).commit_sha
    first = skill_snapshot(workspace.pk, REPOSITORY, PATH, commit)

    assert skill_snapshot(other.pk, REPOSITORY, PATH, commit).path == first.path
    assert github.fetch_tokens == []
