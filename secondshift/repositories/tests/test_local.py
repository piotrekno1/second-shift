import json
import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from secondshift.core.models import Workspace
from secondshift.repositories.local import LocalRepositoryError, attach_local_repository
from secondshift.repositories.models import Repository


def run_git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def create_local_repository(path: Path) -> Path:
    path.mkdir()
    run_git(path, "init", "-b", "main")
    run_git(path, "config", "user.email", "test@secondshift.local")
    run_git(path, "config", "user.name", "SecondShift Test")
    skill = path / ".agents/skills/review/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Review\n")
    (path / ".secondshift.yaml").write_text(
        """
version: 1
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
""".strip()
    )
    run_git(path, "add", ".secondshift.yaml", ".agents")
    run_git(path, "commit", "-m", "Add SecondShift configuration")
    return skill


@pytest.fixture
def workspace(db) -> Workspace:
    user = get_user_model().objects.create_user(username="owner")
    return Workspace.objects.create(name="Test", slug="test", owner=user)


def test_attach_local_repository_discovers_and_syncs_jobs(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    nested = root / ".agents/skills"

    repository = attach_local_repository(workspace, str(nested))

    assert repository.source_type == Repository.SourceType.LOCAL
    assert repository.local_path == str(root.resolve())
    assert repository.default_branch == "main"
    assert repository.config_sha
    assert repository.enabled is True
    assert repository.jobs.get().key == "review"


def test_attach_local_repository_rejects_dirty_skill(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    skill = create_local_repository(root)
    skill.write_text("# Uncommitted review\n")

    with pytest.raises(LocalRepositoryError, match="Commit changes"):
        attach_local_repository(workspace, str(root))


def test_attach_local_repository_rejects_non_repository(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "photos"
    directory.mkdir()

    with pytest.raises(LocalRepositoryError, match="not a Git repository"):
        attach_local_repository(workspace, str(directory))


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=False)
def test_local_repository_api_is_hidden_when_disabled(client) -> None:
    user = get_user_model().objects.create_user(username="owner")
    Workspace.objects.create(name="Test", slug="test", owner=user)
    client.force_login(user)

    response = client.post(
        "/api/repositories/local",
        data=json.dumps({"path": "/tmp/project"}),
        content_type="application/json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_workspace_owner_can_attach_local_repository_through_api(
    client,
    tmp_path: Path,
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    Workspace.objects.create(name="Test", slug="test", owner=user)
    root = tmp_path / "project"
    create_local_repository(root)
    client.force_login(user)

    response = client.post(
        "/api/repositories/local",
        data=json.dumps({"path": str(root)}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["source_type"] == Repository.SourceType.LOCAL
    assert response.json()["display_name"] == "project"
    assert response.json()["local_path"] == str(root)
    assert response.json()["job_count"] == 1

    detail = client.get(f"/api/repositories/{response.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["validation"]["status"] == "valid"
    assert detail.json()["jobs"][0]["name"] == "Review"


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_invalid_manifest_returns_validation_error_instead_of_server_error(
    client,
    tmp_path: Path,
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    Workspace.objects.create(name="Test", slug="test", owner=user)
    root = tmp_path / "project"
    create_local_repository(root)
    (root / ".secondshift.yaml").write_text(
        """
version: 1
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
    secrets: [TOKEN]
""".strip()
    )
    run_git(root, "add", ".secondshift.yaml")
    run_git(root, "commit", "-m", "Add invalid configuration")
    client.force_login(user)

    response = client.post(
        "/api/repositories/local",
        data=json.dumps({"path": str(root)}),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert "Invalid .secondshift.yaml" in response.json()["detail"]


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_resync_and_soft_remove_local_repository(client, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    Workspace.objects.create(name="Test", slug="test", owner=user)
    root = tmp_path / "project"
    create_local_repository(root)
    client.force_login(user)
    created = client.post(
        "/api/repositories/local",
        data=json.dumps({"path": str(root)}),
        content_type="application/json",
    ).json()
    repository = Repository.objects.get(pk=created["id"])
    previous_revision = repository.config_sha

    (root / ".secondshift.yaml").write_text(
        (root / ".secondshift.yaml").read_text().replace("Review", "Health review")
    )
    run_git(root, "add", ".secondshift.yaml")
    run_git(root, "commit", "-m", "Update job name")

    detail = client.get(f"/api/repositories/{repository.pk}")
    assert detail.json()["validation"]["needs_sync"] is True
    resynced = client.post(f"/api/repositories/{repository.pk}/resync")
    assert resynced.status_code == 200
    assert resynced.json()["revision"] != previous_revision
    assert resynced.json()["jobs"][0]["name"] == "Health review"

    repository.jobs.update(enabled=True)
    removed = client.delete(f"/api/repositories/{repository.pk}")
    assert removed.status_code == 204
    repository.refresh_from_db()
    assert repository.removed_at is not None
    assert repository.jobs.filter(enabled=True).count() == 0
    assert client.get("/api/repositories").json() == []
