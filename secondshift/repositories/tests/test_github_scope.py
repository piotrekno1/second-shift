from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from secondshift.connections.models import Connection
from secondshift.jobs.models import JobGrant
from secondshift.repositories.github import github_remote_name, github_repository_scope
from secondshift.repositories.local import (
    attach_local_repository,
    git,
    inspect_local_repository,
)
from secondshift.repositories.models import Repository
from secondshift.repositories.tests.test_local import create_local_repository
from secondshift.workspaces.services import create_workspace


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/example/project.git",
        "git@github.com:example/project.git",
        "ssh://git@github.com/example/project.git",
        "https://token@github.com/example/project.git",
    ],
)
def test_github_origin_identity(remote: str) -> None:
    assert github_remote_name(remote) == "example/project"


@pytest.mark.parametrize(
    "remote",
    [
        "",
        "/tmp/repo",
        "https://gitlab.com/example/project",
        "https://github.com.evil.test/example/project",
        "https://github.com/example/project/extra",
    ],
)
def test_other_origins_have_no_github_scope(remote: str) -> None:
    assert github_remote_name(remote) is None


def test_local_repository_requires_origin_identity() -> None:
    repository = Repository(source_type="local", full_name="local/internal-1234")
    assert github_repository_scope(repository) is None
    repository.clone_url = "https://github.com/example/project"
    assert github_repository_scope(repository) == "example/project"


def test_inspection_records_sanitized_github_origin(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "SKILL.md").write_text("Review the repository.")
    (tmp_path / ".secondshift.yaml").write_text(
        'version: 1\njobs:\n  review:\n    name: Review\n    skill: SKILL.md\n    schedule: "0 9 * * 1"\n'
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "fixture")
    git(
        tmp_path,
        "remote",
        "add",
        "origin",
        "https://private-token@github.com/example/project.git",
    )
    assert (
        inspect_local_repository(str(tmp_path)).clone_url
        == "https://github.com/example/project"
    )
    git(tmp_path, "remote", "remove", "origin")
    assert inspect_local_repository(str(tmp_path)).clone_url == ""


@pytest.mark.django_db
def test_changing_origin_requires_a_new_approval(tmp_path: Path) -> None:

    owner = get_user_model().objects.create_user(username="origin-owner")
    workspace = create_workspace(name="Origin", slug="origin", owner=owner)
    Connection.objects.create(
        workspace=workspace,
        provider="github",
        kind="mcp",
        binding_key="github",
        display_name="GitHub",
        available_tools=["search_pull_requests"],
        require_approval=True,
    )
    root = tmp_path / "project"
    create_local_repository(root)
    manifest = root / ".secondshift.yaml"
    manifest.write_text(
        manifest.read_text()
        + "\n    requires:\n      mcps:\n        - binding: github\n          tools: [search_pull_requests]\n"
    )
    git(root, "add", ".secondshift.yaml")
    git(root, "commit", "-m", "Request GitHub")
    git(root, "remote", "add", "origin", "git@github.com:example/first.git")
    repository = attach_local_repository(workspace, str(root))
    grant = JobGrant.objects.get(capability_request__job__repository=repository)
    grant.status = "approved"
    grant.save()
    attach_local_repository(workspace, str(root))
    grant.refresh_from_db()
    assert grant.status == "approved"
    git(root, "remote", "set-url", "origin", "git@github.com:example/second.git")
    attach_local_repository(workspace, str(root))
    grant.refresh_from_db()
    assert grant.status == "pending"
