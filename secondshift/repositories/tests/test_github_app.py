import base64
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth import get_user_model
from django.test import Client

from secondshift.connections.models import Connection
from secondshift.repositories import autosync, github_app, github_sync
from secondshift.repositories.github_app import STATE_KEY, GitHubAppError
from secondshift.repositories.models import Repository
from secondshift.repositories.tests.test_local import create_local_repository, run_git
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.runner.repositories import import_bundle
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


def test_app_jwt_is_signed_and_short_lived(app_settings: Any) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app_settings.GITHUB_APP_PRIVATE_KEY = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    header, payload, signature = github_app.app_token().split(".")

    def decode(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    data = json.loads(decode(payload))
    assert data["iss"] == "123"
    assert data["iat"] < time.time() < data["exp"] < time.time() + 600
    key.public_key().verify(
        decode(signature),
        f"{header}.{payload}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_installation_token_is_repository_scoped(monkeypatch: Any) -> None:
    request = Mock(return_value={"token": "installation-secret"})
    monkeypatch.setattr(github_app, "request_json", request)
    monkeypatch.setattr(github_app, "app_token", lambda: "jwt")
    assert github_app.installation_token("42", 7) == "installation-secret"
    assert request.call_args.kwargs["json"] == {
        "permissions": {"contents": "read"},
        "repository_ids": [7],
    }


def test_start_is_owner_only_and_callback_rejects_forgery(
    owner_client: Any, workspace: Any, app_settings: Any
) -> None:
    response = owner_client.get("/-/github/connect/")
    assert response.status_code == 302
    state = parse_qs(urlparse(response.url).query)["state"][0]
    assert state
    assert (
        owner_client.get(
            "/-/github/callback/", {"state": "forged", "installation_id": "999"}
        ).status_code
        == 400
    )
    membership = workspace.memberships.get()
    membership.role = WorkspaceMembership.Role.ADMIN
    membership.save()
    assert owner_client.get("/-/github/connect/").status_code == 403


def test_callback_links_only_verified_installations(
    owner_client: Any, workspace: Any, app_settings: Any, monkeypatch: Any
) -> None:
    owner_client.get("/-/github/connect/")
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
    monkeypatch.setattr("secondshift.repositories.github_views.request_json", requests)
    result = owner_client.get(
        "/-/github/callback/",
        {"state": state, "code": "code", "installation_id": "666"},
    )
    assert result.status_code == 302
    connection = Connection.objects.get(workspace=workspace)
    assert connection.external_account_id == "42"
    assert connection.kind == Connection.Kind.SOURCE
    assert connection.access_token == ""
    assert (
        owner_client.get(
            "/-/github/callback/", {"state": state, "code": "code"}
        ).status_code
        == 400
    )


def test_callback_expiry_and_workspace_binding(
    owner_client: Any, workspace: Any, app_settings: Any
) -> None:
    owner_client.get("/-/github/connect/")
    session = owner_client.session
    pending = session[STATE_KEY]
    pending["time"] = time.time() - 601
    session[STATE_KEY] = pending
    session.save()
    assert (
        owner_client.get(
            "/-/github/callback/", {"state": pending["state"], "code": "code"}
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


def test_bundle_import_pins_commit_without_credentials(
    tmp_path: Any, settings: Any
) -> None:
    source = tmp_path / "source"
    create_local_repository(source)
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    run_git(source, "branch", "snapshot")
    bundle = tmp_path / "source.bundle"
    run_git(source, "bundle", "create", str(bundle), "refs/heads/snapshot")
    settings.RUNNER_REPOSITORY_ROOT = tmp_path / "store"
    import_bundle(
        "owner/project", revision, base64.b64encode(bundle.read_bytes()).decode()
    )
    target = settings.RUNNER_REPOSITORY_ROOT / "owner/project"
    assert (
        subprocess.check_output(
            [
                "git",
                "-C",
                str(target),
                "show",
                f"{revision}:.agents/skills/review/SKILL.md",
            ],
            text=True,
        ).strip()
        == "# Review"
    )
    assert "remote" not in (target / "config").read_text()
    with pytest.raises(RunnerProtocolError, match="does not match"):
        import_bundle(
            "owner/project", "a" * 40, base64.b64encode(bundle.read_bytes()).decode()
        )


@pytest.mark.parametrize(
    "name", ["../escape", "owner/..", "/tmp/project", "owner/project/extra"]
)
def test_bundle_cannot_choose_host_paths(
    name: Any, settings: Any, tmp_path: Any
) -> None:
    settings.RUNNER_REPOSITORY_ROOT = tmp_path
    with pytest.raises(RunnerProtocolError):
        import_bundle(name, "a" * 40, "")


def test_sync_fetches_and_publishes_before_advancing_jobs(
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
    monkeypatch.setattr(
        github_sync, "repository_metadata", lambda *args: {"full_name": "owner/project"}
    )
    monkeypatch.setattr(
        github_sync, "installation_token", lambda *args: "github-secret"
    )
    original = github_sync.git_fetch

    def local_fetch(root: Path, token: str, *arguments: str) -> str:
        arguments = tuple(
            str(source)
            if argument == "https://github.com/owner/project.git"
            else argument
            for argument in arguments
        )
        return original(root, token, *arguments)

    monkeypatch.setattr(github_sync, "git_fetch", local_fetch)
    settings.RUNNER_REPOSITORY_ROOT = tmp_path / "store"
    runner = Mock()
    runner.import_repository.side_effect = lambda name, sha, data: import_bundle(
        name, sha, base64.b64encode(data).decode()
    )
    monkeypatch.setattr(github_sync, "get_runner_client", lambda: runner)
    synced = github_sync.sync_github_repository(repository)
    assert synced.config_sha
    assert synced.last_synced_at
    assert synced.jobs.get().key == "review"
    assert synced.enabled is False
    old_revision = synced.config_sha
    run_git(source, "commit", "--allow-empty", "-m", "new commit")
    runner.import_repository.side_effect = RunnerProtocolError("Unavailable")
    with pytest.raises(RunnerProtocolError):
        github_sync.sync_github_repository(synced)
    synced.refresh_from_db()
    assert synced.config_sha == old_revision


def test_revoked_connection_blocks_sync(workspace: Any, source_connection: Any) -> None:
    source_connection.status = Connection.Status.REVOKED
    source_connection.save()
    repository = Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
    )
    with pytest.raises(GitHubAppError, match="disconnected"):
        github_sync.sync_github_repository(repository)


def test_git_failures_never_expose_credentials(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setattr(
        github_sync.subprocess,
        "run",
        lambda *args, **kwargs: Mock(returncode=1, stderr="github-secret"),
    )
    with pytest.raises(GitHubAppError) as caught:
        github_sync.git_fetch(tmp_path, "github-secret", "fetch")
    assert "github-secret" not in str(caught.value)


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
    monkeypatch.setattr(autosync, "github_repository_metadata", lambda repository: {})
    monkeypatch.setattr(autosync, "branch_head", lambda *args: "b" * 40)

    def fail_sync(repository: Any) -> None:
        raise GitHubAppError("The committed manifest is invalid.")

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


def test_github_transport_errors_are_sanitized(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        github_app.requests,
        "request",
        Mock(side_effect=requests.ConnectionError("private-secret")),
    )
    with pytest.raises(GitHubAppError) as caught:
        github_app.request_json("GET", "https://api.github.com/user", "private-secret")
    assert "private-secret" not in str(caught.value)


def test_repository_rename_does_not_reuse_existing_scope(
    workspace: Any, source_connection: Any, monkeypatch: Any
) -> None:
    repository = Repository.objects.create(
        workspace=workspace,
        github_connection=source_connection,
        full_name="owner/project",
        external_id="7",
    )
    monkeypatch.setattr(
        github_sync,
        "repository_metadata",
        lambda *args: {"full_name": "another/project"},
    )
    with pytest.raises(GitHubAppError, match="renamed"):
        github_sync.github_repository_metadata(repository)


def test_new_user_is_sent_to_install_after_authorization(
    owner_client: Any, workspace: Any, app_settings: Any, monkeypatch: Any
) -> None:
    response = owner_client.get("/-/github/connect/")
    assert response.url.startswith("https://github.com/login/oauth/authorize?")
    state = owner_client.session[STATE_KEY]["state"]
    monkeypatch.setattr(
        "secondshift.repositories.github_views.request_json",
        Mock(side_effect=[{"access_token": "user-secret"}, {"installations": []}]),
    )
    response = owner_client.get("/-/github/callback/", {"state": state, "code": "code"})
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
        Mock(side_effect=GitHubAppError("Access revoked.")),
    )
    response = owner_client.get("/api/repositories/github/available")
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["error"]


def test_oauth_exchange_does_not_send_an_empty_bearer_token(monkeypatch: Any) -> None:
    request = Mock(return_value=Mock(json=lambda: {"access_token": "user-secret"}))
    monkeypatch.setattr(github_app.requests, "request", request)
    github_app.request_json(
        "POST", github_app.OAUTH_URL + "/access_token", json={"code": "code"}
    )
    assert "Authorization" not in request.call_args.kwargs["headers"]


def test_branch_listing_is_scoped_to_selected_repository(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        github_app, "repository_metadata", lambda *args: {"full_name": "owner/project"}
    )
    token = Mock(return_value="installation-secret")
    monkeypatch.setattr(github_app, "installation_token", token)
    request = Mock(
        side_effect=[
            [{"name": f"branch-{index}"} for index in range(100)],
            [{"name": "main"}],
        ]
    )
    monkeypatch.setattr(github_app, "request_payload", request)
    branches = github_app.repository_branches("42", 7)
    assert len(branches) == 101
    assert branches[-1] == "main"
    token.assert_called_once_with("42", 7)
    assert "page=2" in request.call_args.args[1]


def test_branch_endpoint_enforces_workspace_access(
    owner_client: Any, source_connection: Any, monkeypatch: Any
) -> None:
    branches = Mock(return_value=["main", "release"])
    monkeypatch.setattr("secondshift.repositories.api.repository_branches", branches)
    url = f"/api/repositories/github/{source_connection.pk}/7/branches"
    response = owner_client.get(url)
    assert response.status_code == 200
    assert response.json() == ["main", "release"]
    branches.assert_called_once_with("42", 7)
    user = get_user_model().objects.create_user(username="branch-outsider")
    other = create_workspace(name="Other", slug="branch-other", owner=user)
    source_connection.workspace = other
    source_connection.save()
    assert owner_client.get(url).status_code == 404
    assert branches.call_count == 1
