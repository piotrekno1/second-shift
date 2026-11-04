from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from secondshift.repositories.local import attach_local_repository
from secondshift.repositories.models import Repository
from secondshift.repositories.tests.test_local import create_local_repository
from secondshift.workspaces.models import Workspace
from secondshift.workspaces.services import workspace_owner


def github_repository(workspace: Workspace) -> Repository:
    return Repository.objects.create(
        workspace=workspace,
        full_name="acme/web",
        external_id="42",
        enabled=True,
        config_sha="a" * 40,
    )


def post(client: Client, repository: Repository, allow: bool, run_ci: bool) -> object:
    return client.post(
        f"/api/repositories/{repository.pk}/pull-requests",
        {"allow": allow, "run_ci": run_ci},
        content_type="application/json",
    )


def test_only_owners_let_tasks_open_pull_requests(
    client: Client, workspace: Workspace
) -> None:
    repository = github_repository(workspace)
    admin = get_user_model().objects.create_user(username="admin")
    workspace.memberships.create(user=admin, role="admin")

    client.force_login(admin)
    refused = post(client, repository, True, False)
    client.force_login(workspace_owner(workspace))
    allowed = post(client, repository, True, False)

    assert refused.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["allow_pull_requests"] is True
    assert allowed.json()["pull_request_ci"] is False
    repository.refresh_from_db()
    assert repository.allow_pull_requests is True


def test_ci_is_off_unless_pull_requests_are_allowed(
    client: Client, workspace: Workspace
) -> None:
    repository = github_repository(workspace)
    client.force_login(workspace_owner(workspace))

    assert post(client, repository, True, True).json()["pull_request_ci"] is True
    turned_off = post(client, repository, False, True).json()

    assert turned_off["allow_pull_requests"] is False
    assert turned_off["pull_request_ci"] is False


@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_local_repositories_cannot_receive_pull_requests(
    client: Client, workspace: Workspace, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    repository = attach_local_repository(workspace, str(root))
    client.force_login(workspace_owner(workspace))

    response = post(client, repository, True, False)

    assert response.status_code == 409
    detail = client.get(f"/api/repositories/{repository.pk}").json()
    assert detail["can_open_pull_requests"] is False
    assert detail["allow_pull_requests"] is False


def post_issues(client: Client, repository: Repository, allow: bool) -> object:
    return client.post(
        f"/api/repositories/{repository.pk}/issues",
        {"allow": allow},
        content_type="application/json",
    )


def test_only_owners_let_jobs_open_issues(client: Client, workspace: Workspace) -> None:
    repository = github_repository(workspace)
    admin = get_user_model().objects.create_user(username="admin")
    workspace.memberships.create(user=admin, role="admin")

    client.force_login(admin)
    refused = post_issues(client, repository, True)
    client.force_login(workspace_owner(workspace))
    allowed = post_issues(client, repository, True)

    assert refused.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["allow_issues"] is True
    repository.refresh_from_db()
    assert repository.allow_issues is True
    assert post_issues(client, repository, False).json()["allow_issues"] is False


@override_settings(LOCAL_REPOSITORIES_ENABLED=True)
def test_local_repositories_cannot_receive_issues(
    client: Client, workspace: Workspace, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    create_local_repository(root)
    repository = attach_local_repository(workspace, str(root))
    client.force_login(workspace_owner(workspace))

    assert post_issues(client, repository, True).status_code == 409
    assert (
        client.get(f"/api/repositories/{repository.pk}").json()["allow_issues"] is False
    )
