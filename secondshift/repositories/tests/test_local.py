import json
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from secondshift.repositories.local import (LocalRepositoryError, attach_local_repository, local_directories)
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace, WorkspaceMembership
from secondshift.workspaces.services import create_workspace


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


def test_edits_in_the_working_folder_are_ignored(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    skill = create_local_repository(root)
    skill.write_text("# Uncommitted review\n")
    (root / ".secondshift.yaml").write_text("not: [valid")

    repository = attach_local_repository(workspace, str(root))

    assert repository.jobs.get().name == "Review"


def test_a_repository_follows_its_branch_not_the_checkout(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    repository = attach_local_repository(workspace, str(root))
    run_git(root, "checkout", "-b", "experiment")
    manifest = root / ".secondshift.yaml"
    manifest.write_text(manifest.read_text().replace("Review", "Experiment"))
    run_git(root, "commit", "-am", "Try something")

    resynced = attach_local_repository(workspace, str(root), "main")

    assert resynced.pk == repository.pk
    assert resynced.default_branch == "main"
    assert resynced.jobs.get().name == "Review"


def test_a_skill_cannot_be_a_link(workspace: Workspace, tmp_path: Path) -> None:
    root = tmp_path / "project"
    skill = create_local_repository(root)
    skill.unlink()
    skill.symlink_to("/etc/hosts")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "Point the skill elsewhere")

    with pytest.raises(LocalRepositoryError, match="not a link"):
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
    create_workspace(name="Test", slug="test", owner=user)
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
    create_workspace(name="Test", slug="test", owner=user)
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
    create_workspace(name="Test", slug="test", owner=user)
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


def test_local_directories_lists_subdirectories_only(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "photos").mkdir()
    (root / ".agents").mkdir()
    (root / "notes.txt").write_text("hello")

    candidate, parent, directories = local_directories(str(root))

    assert candidate == root.resolve()
    assert parent == tmp_path
    assert [item.name for item in directories] == [".agents", "photos"]


def test_local_directories_defaults_to_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    candidate, parent, _ = local_directories("")

    assert candidate == tmp_path.resolve()
    assert parent == tmp_path.parent


def test_local_directories_preserves_trailing_whitespace(tmp_path: Path) -> None:
    root = tmp_path / "project "
    root.mkdir()
    (root / "selected").mkdir()
    (tmp_path / "project").mkdir()

    candidate, _, directories = local_directories(str(root))

    assert candidate == root.resolve()
    assert [item.name for item in directories] == ["selected"]


def test_local_directories_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(LocalRepositoryError, match="not a directory"):
        local_directories(str(tmp_path / "missing"))


def test_local_directories_reports_inaccessible_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def denied(path: Path) -> NoReturn:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, "is_dir", denied)

    with pytest.raises(LocalRepositoryError, match="Unable to access"):
        local_directories(str(tmp_path))


def test_local_directories_reports_unreadable_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    child = tmp_path / "child"
    child.mkdir()
    is_directory = Path.is_dir

    def inspect(path: Path) -> bool:
        if path == child:
            raise PermissionError(13, "Permission denied", str(path))
        return is_directory(path)

    monkeypatch.setattr(Path, "is_dir", inspect)

    with pytest.raises(LocalRepositoryError, match="Unable to read"):
        local_directories(str(tmp_path))


def test_local_directories_reports_unreadable_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def denied(path: Path) -> NoReturn:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, "iterdir", denied)

    with pytest.raises(LocalRepositoryError, match="Unable to read"):
        local_directories(str(tmp_path))


def test_local_directories_reports_invalid_path() -> None:
    with pytest.raises(LocalRepositoryError, match="Unable to open"):
        local_directories("\x00")


def test_local_directories_reports_null_parent_at_root() -> None:
    if Path("/").parent != Path("/"):
        pytest.skip("POSIX-only layout")

    _, parent, _ = local_directories("/")

    assert parent is None


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_directories_api_lists_only_directories(client, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    create_workspace(name="Test", slug="test", owner=user)
    client.force_login(user)
    root = tmp_path / "project"
    root.mkdir()
    (root / "photos").mkdir()
    (root / "notes.txt").write_text("hello")

    response = client.get(f"/api/repositories/local/directories?path={root}")

    assert response.status_code == 200
    assert response.json()["path"] == str(root)
    assert response.json()["parent"] == str(tmp_path)
    assert response.json()["directories"] == [
        {"name": "photos", "path": str(root / "photos")}
    ]


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_directories_api_defaults_to_home(
    client,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    create_workspace(name="Test", slug="test", owner=user)
    client.force_login(user)
    monkeypatch.setenv("HOME", str(tmp_path))

    response = client.get("/api/repositories/local/directories")

    assert response.status_code == 200
    assert response.json()["path"] == str(tmp_path.resolve())


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_directories_api_returns_controlled_error_for_overlong_component(
    client,
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    create_workspace(name="Test", slug="test", owner=user)
    client.force_login(user)

    response = client.get(
        "/api/repositories/local/directories",
        {"path": "a" * 1001},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_directories_api_accepts_navigable_paths_over_one_thousand_characters(
    client,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    create_workspace(name="Test", slug="test", owner=user)
    client.force_login(user)
    root = tmp_path / ("a" * 1001)

    def directories(raw_path: str) -> tuple[Path, Path | None, list[Path]]:
        assert raw_path == str(root)
        return root, tmp_path, []

    monkeypatch.setattr("secondshift.core.api.local_directories", directories)

    response = client.get(
        "/api/repositories/local/directories",
        {"path": str(root)},
    )

    assert response.status_code == 200
    assert response.json()["path"] == str(root)


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_directories_api_hides_non_owner(client) -> None:
    owner = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=owner)
    member = get_user_model().objects.create_user(username="member")
    WorkspaceMembership.objects.create(workspace=workspace, user=member)
    client.force_login(member)

    response = client.get("/api/repositories/local/directories?path=")

    assert response.status_code == 403


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=False)
def test_directories_api_is_hidden_when_disabled(client) -> None:
    user = get_user_model().objects.create_user(username="owner")
    create_workspace(name="Test", slug="test", owner=user)
    client.force_login(user)

    response = client.get("/api/repositories/local/directories?path=")

    assert response.status_code == 404


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_directories_api_returns_validation_error_for_missing_path(
    client,
    tmp_path: Path,
) -> None:
    user = get_user_model().objects.create_user(username="owner")
    create_workspace(name="Test", slug="test", owner=user)
    client.force_login(user)

    response = client.get(
        f"/api/repositories/local/directories?path={tmp_path / 'missing'}",
    )

    assert response.status_code == 422
    assert "not a directory" in response.json()["detail"]


@pytest.mark.django_db
@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_resync_and_soft_remove_local_repository(client, tmp_path: Path) -> None:
    user = get_user_model().objects.create_user(username="owner")
    create_workspace(name="Test", slug="test", owner=user)
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
