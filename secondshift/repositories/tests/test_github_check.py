from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from secondshift.connections.models import Connection
from secondshift.github import app, permissions
from secondshift.github.errors import GitHubError
from secondshift.github.tokens import TokenOwner
from secondshift.repositories import github_check
from secondshift.repositories.github_access import GitHubAccess
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace

TOKEN = "github_pat_" + "a" * 40
SETTINGS_URL = "https://github.com/settings/installations/42"


def app_source(workspace: Workspace) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.MANAGED,
        external_account_id="42",
        display_name="GitHub · someone",
    )


def token_source(workspace: Workspace) -> Connection:
    return Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.API_KEY,
        external_account_id="token:someone",
        display_name="GitHub · someone",
        access_token=TOKEN,
    )


def add_repository(connection: Connection, name: str, external_id: str) -> Repository:
    return Repository.objects.create(
        workspace=connection.workspace,
        github_connection=connection,
        full_name=name,
        external_id=external_id,
    )


@pytest.fixture
def reachable(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Repositories GitHub answers for. Others fail like a lost grant."""
    names = {"owner/project"}

    def access(repository: Repository) -> GitHubAccess:
        if repository.full_name not in names:
            raise GitHubError("GitHub access failed.")
        metadata = {"default_branch": "main"}
        return GitHubAccess(7, repository.full_name, "minted", metadata)

    monkeypatch.setattr(github_check, "github_access", access)
    return names


def installation(monkeypatch: pytest.MonkeyPatch, granted: dict[str, str]) -> None:
    monkeypatch.setattr(
        app,
        "installation",
        Mock(return_value=app.InstallationDetails(granted, SETTINGS_URL)),
    )


def by_feature(connection: Connection) -> dict[str, dict]:
    connection.refresh_from_db()
    check = connection.metadata["last_check"]
    return {feature["feature"]: feature for feature in check["features"]}


def test_an_app_with_every_permission_passes(
    workspace: Workspace, reachable: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = app_source(workspace)
    add_repository(connection, "owner/project", "7")
    installation(
        monkeypatch,
        {
            "contents": "write",
            "pull_requests": "write",
            "actions": "read",
            "statuses": "read",
        },
    )
    result = github_check.check_source(connection)
    assert result.ok
    assert result.message == "Reaches its repository."
    assert {key: value["ok"] for key, value in by_feature(connection).items()} == {
        "sync": True,
        "tools": True,
        "draft_pull_requests": True,
    }
    assert connection.metadata["last_check"]["fix_url"] == SETTINGS_URL


def test_an_app_names_each_missing_permission(
    workspace: Workspace, reachable: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = app_source(workspace)
    add_repository(connection, "owner/project", "7")
    installation(monkeypatch, {"contents": "read", "pull_requests": "read"})
    result = github_check.check_source(connection)
    # Missing tool or draft access does not stop sync, so the check still passes.
    assert result.ok
    features = by_feature(connection)
    assert features["tools"]["ok"] is False
    assert features["tools"]["message"] == (
        "Needs read access to Actions, Commit statuses."
    )
    assert features["draft_pull_requests"]["message"] == (
        "Needs write access to Contents, Pull requests."
    )


def test_an_app_without_contents_fails(
    workspace: Workspace, reachable: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = app_source(workspace)
    installation(monkeypatch, {"metadata": "read"})
    result = github_check.check_source(connection)
    assert not result.ok
    assert by_feature(connection)["sync"]["ok"] is False


def test_a_removed_installation_fails(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = app_source(workspace)
    monkeypatch.setattr(app, "installation", Mock(side_effect=GitHubError("gone")))
    result = github_check.check_source(connection)
    assert not result.ok
    assert "removed" in result.message
    assert by_feature(connection) == {}


def test_unreachable_repositories_are_named(
    workspace: Workspace, reachable: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = app_source(workspace)
    add_repository(connection, "owner/project", "7")
    add_repository(connection, "owner/private", "8")
    installation(monkeypatch, {"contents": "read"})
    result = github_check.check_source(connection)
    assert not result.ok
    assert result.message.startswith("Cannot reach owner/private.")


def test_a_token_is_probed_and_its_expiry_refreshed(
    workspace: Workspace, reachable: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = token_source(workspace)
    add_repository(connection, "owner/project", "7")
    expires = datetime(2027, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(
        github_check, "inspect_token", Mock(return_value=TokenOwner("someone", expires))
    )
    reads = Mock(
        return_value={
            "contents": True,
            "pull_requests": True,
            "actions": False,
            "statuses": True,
        }
    )
    monkeypatch.setattr(permissions, "token_reads", reads)
    result = github_check.check_source(connection)
    assert result.ok
    assert reads.call_args.args == (
        TOKEN,
        "owner/project",
        "main",
        ("contents", "pull_requests", "actions", "statuses"),
    )
    features = by_feature(connection)
    assert features["tools"]["message"] == "Needs read access to Actions."
    # Write access cannot be probed without writing.
    assert features["draft_pull_requests"]["ok"] is None
    assert connection.expires_at == expires
    assert TOKEN not in str(connection.metadata)


def test_a_refused_token_fails_with_a_link_to_replace_it(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = token_source(workspace)
    monkeypatch.setattr(
        github_check,
        "inspect_token",
        Mock(
            side_effect=GitHubError("GitHub refused this token. It may have expired.")
        ),
    )
    result = github_check.check_source(connection)
    assert not result.ok
    assert result.fix_url == github_check.GITHUB_TOKENS_URL


def test_a_token_without_repositories_cannot_check_features(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = token_source(workspace)
    monkeypatch.setattr(
        github_check, "inspect_token", Mock(return_value=TokenOwner("someone", None))
    )
    result = github_check.check_source(connection)
    assert result.ok
    assert result.message == "Connected. No repositories added yet."
    assert {value["ok"] for value in by_feature(connection).values()} == {None}


def test_testing_a_source_runs_the_check(
    workspace: Workspace, reachable: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = app_source(workspace)
    installation(monkeypatch, {"contents": "read"})
    client = Client()
    client.force_login(get_user_model().objects.get(username="owner"))
    response = client.post(f"/api/connections/{connection.pk}/test")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    listed: Any = client.get("/api/connections").json()
    check = next(item for item in listed if item["id"] == connection.pk)["last_check"]
    assert check["fix_url"] == SETTINGS_URL
    assert [feature["feature"] for feature in check["features"]] == [
        "sync",
        "tools",
        "draft_pull_requests",
    ]
