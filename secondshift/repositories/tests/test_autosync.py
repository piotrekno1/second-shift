import subprocess
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from secondshift.repositories.autosync import sync_moved_repositories
from secondshift.repositories.local import attach_local_repository
from secondshift.repositories.tests.test_local import create_local_repository, run_git
from secondshift.workspaces.models import Workspace


def head_of(path: Path, branch: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def rename_job(root: Path, name: str) -> None:
    manifest = root / ".secondshift.yaml"
    manifest.write_text(manifest.read_text().replace("name: Review", f"name: {name}"))
    run_git(root, "commit", "-am", f"Rename the job to {name}")


def test_new_commits_on_the_branch_are_synced(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    repository = attach_local_repository(workspace, str(root))

    assert sync_moved_repositories() == 0

    rename_job(root, "Weekly review")

    assert sync_moved_repositories() == 1
    repository.refresh_from_db()
    assert repository.jobs.get().name == "Weekly review"
    assert repository.config_sha == head_of(root, "main")


def test_a_broken_commit_keeps_the_last_good_sync(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    repository = attach_local_repository(workspace, str(root))
    good = repository.config_sha
    (root / ".secondshift.yaml").write_text("not: [valid")
    run_git(root, "commit", "-am", "Break the manifest")

    assert sync_moved_repositories() == 0

    repository.refresh_from_db()
    assert repository.config_sha == good
    assert repository.jobs.get().name == "Review"


def test_disabled_repositories_are_left_alone(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    repository = attach_local_repository(workspace, str(root))
    repository.enabled = False
    repository.save(update_fields=["enabled"])
    rename_job(root, "Weekly review")

    assert sync_moved_repositories() == 0
    assert repository.jobs.get().name == "Review"


def test_a_repository_can_turn_auto_sync_off(
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    repository = attach_local_repository(workspace, str(root))
    repository.auto_sync = False
    repository.save(update_fields=["auto_sync"])
    rename_job(root, "Weekly review")

    assert sync_moved_repositories() == 0
    # Resync still works by hand, and keeps the choice.
    resynced = attach_local_repository(workspace, str(root), "main")
    assert resynced.jobs.get().name == "Weekly review"
    assert resynced.auto_sync is False


@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_admins_switch_auto_sync_and_members_cannot(
    client: Client,
    workspace: Workspace,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    repository = attach_local_repository(workspace, str(root))
    users = get_user_model().objects
    admin = users.create_user(username="admin")
    member = users.create_user(username="member")
    workspace.memberships.create(user=admin, role="admin")
    workspace.memberships.create(user=member, role="member")
    path = f"/api/repositories/{repository.pk}/auto-sync"

    client.force_login(member)
    refused = client.post(path, {"enabled": False}, content_type="application/json")
    client.force_login(admin)
    switched = client.post(path, {"enabled": False}, content_type="application/json")

    assert refused.status_code == 403
    assert switched.status_code == 200
    assert switched.json()["auto_sync"] is False
    repository.refresh_from_db()
    assert repository.auto_sync is False
