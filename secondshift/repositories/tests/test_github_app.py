import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.utils import timezone

from secondshift.connections.models import Connection
from secondshift.github import repos, rest
from secondshift.github.errors import GitHubError
from secondshift.repositories import autosync, github_sync
from secondshift.repositories.github_access import github_access
from secondshift.repositories.github_views import STATE_KEY
from secondshift.repositories.mirrors import SYNCED_REF, mirror_path
from secondshift.repositories.models import Repository
from secondshift.repositories.tests.github_fakes import fake_github
from secondshift.repositories.tests.test_local import (
    commit_configuration,
    create_local_repository,
    run_git,
)
from secondshift.workspaces.models import WorkspaceMembership
from secondshift.workspaces.services import create_workspace


@pytest.fixture
def app_settings(settings: Any) -> Any:
    settings.GITHUB_APP_ID = "123"
    settings.GITHUB_APP_SLUG = "secondshift-test"
    settings.GITHUB_APP_CLIENT_ID = "client"
    settings.GITHUB_APP_CLIENT_SECRET = "secret"
    settings.GITHUB_APP_PRIVATE_KEY = "configured-key"
    return settings


@pytest.fixture
def owner_client(workspace: Any) -> Any:
    client = Client()
    client.force_login(get_user_model().objects.get(username="owner"))
    return client


@pytest.fixture
def source_connection(workspace: Any) -> Any:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.MANAGED,
        external_account_id="42",
        display_name="GitHub",
    )


def test_start_is_owner_only_and_callback_rejects_forgery(
    owner_client: Any, workspace: Any, app_settings: Any
) -> None:
    response = owner_client.get("/github/connect/")
    assert response.status_code == 302
    state = parse_qs(urlparse(response.url).query)["state"][0]
    assert state
    assert (
        owner_client.get(
            "/github/callback/", {"state": "forged", "installation_id": "999"}
        ).status_code
        == 400
    )
    membership = workspace.memberships.get()
    membership.role = WorkspaceMembership.Role.ADMIN
    membership.save()
    assert owner_client.get("/github/connect/").status_code == 403


def test_callback_links_only_verified_installations(
    owner_client: Any, workspace: Any, app_settings: Any, monkeypatch: Any
) -> None:
    owner_client.get("/github/connect/")
    state = owner_client.session[STATE_KEY]["state"]
    requests = Mock(
        side_effect=[
            {"access_token": "user-secret"},
            {
                "installations": [
                    {"id": 42, "app_id": 123, "account": {"login": "allowed"}},
                    {"id": 99, "app_id": 999, "account": {"login": "other-app"}},
                ]
            },
        ]
    )
    monkeypatch.setattr(rest, "request_payload", requests)
    result = owner_client.get(
        "/github/callback/",
        {"state": state, "code": "code", "installation_id": "666"},
    )
    assert result.status_code == 302
    connection = Connection.objects.get(workspace=workspace)
    assert connection.external_account_id == "42"
    assert connection.kind == Connection.Kind.SOURCE
    assert connection.access_token == ""
    assert (
        owner_client.get(
            "/github/callback/", {"state": state, "code": "code"}
        ).status_code
        == 400
    )


def test_callback_expiry_and_workspace_binding(
    owner_client: Any, workspace: Any, app_settings: Any
) -> None:
    owner_client.get("/github/connect/")
    session = owner_client.session
    pending = session[STATE_KEY]
    pending["time"] = time.time() - 601
    session[STATE_KEY] = pending
    session.save()
    assert (
        owner_client.get(
            "/github/callback/", {"state": pending["state"], "code": "code"}
        ).status_code
        == 400
    )


def test_available_repositories_enforces_workspace(
    owner_client: Any, source_connection: Any, app_settings: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        "secondshift.repositories.github_sync.installation_repositories",
        lambda installation: [
            {"id": 7, "full_name": "owner/project", "default_branch": "main"}
        ],
    )
    result = owner_client.get("/api/repositories/github/available")
    assert result.status_code == 200
    assert result.json()["repositories"] == [
        {
            "connection_id": source_connection.pk,
            "repository_id": 7,
            "full_name": "owner/project",
            "default_branch": "main",
        }
    ]


def synced_ref(mirror: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(mirror), "rev-parse", SYNCED_REF], text=True
    ).strip()


def test_sync_fetches_into_the_mirror_before_advancing_jobs(
    workspace: Any,
    source_connection: Any,
    tmp_path: Any,
    monkeypatch: Any,
    settings: Any,
) -> None:
    source = tmp_path / "source"
    create_local_repository(source)
    repository = Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
        installation_id="42",
    )
    fake_github(monkeypatch)
    original = github_sync.authenticated_git
    reachable = True

    def local_fetch(root: Path, token: str, *arguments: str) -> str:
        if "fetch" in arguments and not reachable:
            raise GitHubError("Could not fetch the GitHub repository and branch.")
        arguments = tuple(
            str(source)
            if argument == "https://github.com/owner/project.git"
            else argument
            for argument in arguments
        )
        return original(root, token, *arguments)

    monkeypatch.setattr(github_sync, "authenticated_git", local_fetch)
    settings.SOURCE_CACHE_ROOT = tmp_path / "sources"
    synced = github_sync.sync_github_repository(repository)
    assert synced.config_sha
    assert synced.last_synced_at
    assert synced.jobs.get().key == "review"
    assert synced.enabled is False
    mirror = mirror_path(repository.pk)
    assert synced_ref(mirror) == synced.config_sha
    assert "github-secret" not in (mirror / "config").read_text()

    run_git(source, "commit", "--allow-empty", "-m", "new commit")
    synced = github_sync.sync_github_repository(synced)
    assert synced_ref(mirror) == synced.config_sha

    old_revision = synced.config_sha
    run_git(source, "commit", "--allow-empty", "-m", "unreachable commit")
    reachable = False
    with pytest.raises(GitHubError):
        github_sync.sync_github_repository(synced)
    synced.refresh_from_db()
    assert synced.config_sha == old_revision


def fetch_from_local_source(
    monkeypatch: Any, source: Path, settings: Any, tmp_path: Path
) -> None:
    """Point GitHub fetches for owner/project at a local repository."""
    fake_github(monkeypatch)
    original = github_sync.authenticated_git

    def local_fetch(root: Path, token: str, *arguments: str) -> str:
        arguments = tuple(
            str(source)
            if argument == "https://github.com/owner/project.git"
            else argument
            for argument in arguments
        )
        return original(root, token, *arguments)

    monkeypatch.setattr(github_sync, "authenticated_git", local_fetch)
    settings.SOURCE_CACHE_ROOT = tmp_path / "sources"


def test_import_without_a_manifest_keeps_the_repository(
    workspace: Any,
    source_connection: Any,
    tmp_path: Any,
    monkeypatch: Any,
    settings: Any,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run_git(source, "init", "-b", "main")
    run_git(source, "config", "user.email", "test@secondshift.local")
    run_git(source, "config", "user.name", "SecondShift Test")
    run_git(source, "commit", "--allow-empty", "-m", "Start")
    fetch_from_local_source(monkeypatch, source, settings, tmp_path)

    repository = github_sync.attach_github_repository(
        workspace, source_connection, 7, ""
    )

    assert repository.pk
    assert repository.config_sha == ""
    assert repository.checked_sha
    assert "no committed .secondshift.yaml" in repository.last_sync_error
    assert not repository.jobs.exists()
    assert repository.enabled is False


def test_autosync_picks_up_the_first_manifest_of_a_disabled_repository(
    workspace: Any,
    source_connection: Any,
    tmp_path: Any,
    monkeypatch: Any,
    settings: Any,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run_git(source, "init", "-b", "main")
    run_git(source, "config", "user.email", "test@secondshift.local")
    run_git(source, "config", "user.name", "SecondShift Test")
    run_git(source, "commit", "--allow-empty", "-m", "Start")
    fetch_from_local_source(monkeypatch, source, settings, tmp_path)
    repository = Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
    )
    github_sync.sync_github_repository(repository)
    monkeypatch.setattr(
        repos,
        "branch_head",
        lambda *args: subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "main"], text=True
        ).strip(),
    )
    fetches = Mock(wraps=autosync.sync_github_repository)
    monkeypatch.setattr(autosync, "sync_github_repository", fetches)

    # The commit without a manifest was already read, so it is not fetched again.
    assert autosync.sync_moved_repositories() == 0
    assert fetches.call_count == 0

    commit_configuration(source)
    assert autosync.sync_moved_repositories() == 1
    repository.refresh_from_db()
    assert repository.config_sha
    assert repository.last_sync_error == ""
    assert repository.jobs.get().key == "review"
    assert repository.enabled is False


def test_revoked_connection_blocks_sync(workspace: Any, source_connection: Any) -> None:
    source_connection.status = Connection.Status.REVOKED
    source_connection.save()
    repository = Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
    )
    with pytest.raises(GitHubError, match="disconnected"):
        github_sync.sync_github_repository(repository)


def test_failed_poll_is_visible_and_retains_last_revision(
    workspace: Any, source_connection: Any, monkeypatch: Any
) -> None:
    repository = Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
        config_sha="a" * 40,
        enabled=True,
    )
    fake_github(monkeypatch)
    monkeypatch.setattr(repos, "branch_head", lambda *args: "b" * 40)

    def fail_sync(repository: Any) -> None:
        raise GitHubError("The committed manifest is invalid.")

    monkeypatch.setattr(autosync, "sync_github_repository", fail_sync)
    assert autosync.sync_moved_repositories() == 0
    repository.refresh_from_db()
    assert repository.config_sha == "a" * 40
    assert repository.last_sync_error == "The committed manifest is invalid."


def test_import_rejects_another_workspaces_connection(
    owner_client: Any, source_connection: Any
) -> None:
    user = get_user_model().objects.create_user(username="other")
    other = create_workspace(name="Other", slug="other", owner=user)
    source_connection.workspace = other
    source_connection.save()
    response = owner_client.post(
        "/api/repositories/github",
        {"connection_id": source_connection.pk, "repository_id": 7},
        content_type="application/json",
    )
    assert response.status_code == 404


def test_repository_rename_does_not_reuse_existing_scope(
    workspace: Any, source_connection: Any, monkeypatch: Any
) -> None:
    repository = Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
    )
    fake_github(monkeypatch, full_name="another/project")
    with pytest.raises(GitHubError, match="renamed"):
        github_access(repository)


def test_new_user_is_sent_to_install_after_authorization(
    owner_client: Any, workspace: Any, app_settings: Any, monkeypatch: Any
) -> None:
    response = owner_client.get("/github/connect/")
    assert response.url.startswith("https://github.com/login/oauth/authorize?")
    state = owner_client.session[STATE_KEY]["state"]
    monkeypatch.setattr(
        rest,
        "request_payload",
        Mock(side_effect=[{"access_token": "user-secret"}, {"installations": []}]),
    )
    response = owner_client.get("/github/callback/", {"state": state, "code": "code"})
    assert response.url.startswith(
        "https://github.com/apps/secondshift-test/installations/new?"
    )
    assert owner_client.session[STATE_KEY]["state"] != state


def test_failed_installation_still_offers_reconnection(
    owner_client: Any, source_connection: Any, app_settings: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        github_sync,
        "installation_repositories",
        Mock(side_effect=GitHubError("Access revoked.")),
    )
    response = owner_client.get("/api/repositories/github/available")
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["error"]


def test_branch_endpoint_enforces_workspace_access(
    owner_client: Any, source_connection: Any, monkeypatch: Any
) -> None:
    branches = Mock(return_value=["main", "release"])
    monkeypatch.setattr("secondshift.repositories.api.repository_branches", branches)
    url = f"/api/repositories/github/{source_connection.pk}/7/branches"
    response = owner_client.get(url)
    assert response.status_code == 200
    assert response.json() == ["main", "release"]
    branches.assert_called_once_with(source_connection, 7)
    user = get_user_model().objects.create_user(username="branch-outsider")
    other = create_workspace(name="Other", slug="branch-other", owner=user)
    source_connection.workspace = other
    source_connection.save()
    assert owner_client.get(url).status_code == 404
    assert branches.call_count == 1


@pytest.mark.django_db
def test_session_says_whether_the_github_app_is_configured(
    owner_client: Any, settings: Any
) -> None:
    settings.GITHUB_APP_ID = ""
    assert owner_client.get("/api/me").json()["github_app_configured"] is False


@pytest.mark.django_db
def test_session_reports_the_github_app_without_calling_github(
    owner_client: Any, app_settings: Any
) -> None:
    with patch(
        "secondshift.github.rest.client",
        side_effect=AssertionError("The session must not call GitHub."),
    ):
        body = owner_client.get("/api/me").json()
    assert body["github_app_configured"] is True


@pytest.fixture
def github_metadata(monkeypatch: Any) -> None:
    fake_github(monkeypatch)


def test_adding_a_repository_syncs_outside_a_transaction(
    workspace: Any, source_connection: Any, github_metadata: None, monkeypatch: Any
) -> None:
    outer = len(connection.atomic_blocks)
    depths = []

    def sync(repository: Repository) -> Repository:
        depths.append(len(connection.atomic_blocks))
        return repository

    monkeypatch.setattr(github_sync, "sync_github_repository", sync)

    github_sync.attach_github_repository(workspace, source_connection, 7, "")

    assert depths == [outer]


def test_a_failed_first_sync_removes_the_new_repository(
    workspace: Any, source_connection: Any, github_metadata: None, monkeypatch: Any
) -> None:
    def fail(repository: Repository) -> Repository:
        raise GitHubError("Could not fetch the GitHub repository and branch.")

    monkeypatch.setattr(github_sync, "sync_github_repository", fail)

    with pytest.raises(GitHubError):
        github_sync.attach_github_repository(workspace, source_connection, 7, "")

    assert not Repository.objects.filter(full_name="owner/project").exists()


def test_a_failed_sync_leaves_a_readded_repository_removed(
    workspace: Any, source_connection: Any, github_metadata: None, monkeypatch: Any
) -> None:
    removed_at = timezone.now()
    repository = Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
        installation_id="old",
        default_branch="old-branch",
        removed_at=removed_at,
    )

    def fail(repository: Repository) -> Repository:
        raise GitHubError("Could not fetch the GitHub repository and branch.")

    monkeypatch.setattr(github_sync, "sync_github_repository", fail)

    with pytest.raises(GitHubError):
        github_sync.attach_github_repository(workspace, source_connection, 7, "dev")

    repository.refresh_from_db()
    assert repository.removed_at == removed_at
    assert repository.installation_id == "old"
    assert repository.default_branch == "old-branch"


def test_an_autosync_check_mints_one_token_per_repository(
    workspace: Any, source_connection: Any, monkeypatch: Any
) -> None:
    Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
        checked_sha="a" * 40,
    )
    tokens = fake_github(monkeypatch)
    heads = Mock(return_value="a" * 40)
    monkeypatch.setattr(repos, "branch_head", heads)

    assert autosync.sync_moved_repositories() == 0

    assert tokens.call_count == 1
    assert heads.call_args.args[0] == "github-secret"
