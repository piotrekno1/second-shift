import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.contrib.auth import get_user_model

from secondshift.connections.models import Connection
from secondshift.github import rest
from secondshift.github.errors import GitHubError
from secondshift.repositories import pull_requests
from secondshift.repositories.mirrors import mirror_path
from secondshift.repositories.models import Repository
from secondshift.repositories.pull_requests import (
    DraftPullRequest,
    PublishError,
    commit_patch,
    open_draft,
    publish_pull_request,
)
from secondshift.repositories.tests.github_fakes import fake_github
from secondshift.workspaces.models import Workspace
from secondshift.workspaces.services import create_workspace

DRAFT = DraftPullRequest(
    branch="secondshift/run-7",
    base="main",
    title="Fix stale setup steps",
    body="## What changed\n\nREADME.md now says npm install.",
    message="Fix stale setup steps\n\n[skip ci]",
    committed_at=datetime(2026, 11, 3, 9, 0, tzinfo=UTC),
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, str, Path]:
    """A project, its base commit, and the patch an agent made on top of it."""
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q")
    (root / "README.md").write_text("Run npm ci.\n")
    (root / "logo.bin").write_bytes(bytes(range(256)))
    git(root, "add", ".")
    git(root, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "README.md").write_text("Run npm install.\n")
    (root / "logo.bin").write_bytes(bytes(reversed(range(256))))
    (root / "NEW.md").write_text("added\n")
    git(root, "add", "-A")
    patch = tmp_path / "changes.patch"
    patch.write_text(git(root, "diff", "--cached", "--binary", "--full-index") + "\n")
    git(root, "reset", "-q", "--hard")
    return root, base, patch


def bare_mirror(source_root: Path, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(source_root), str(path)], check=True
    )
    return path


def test_the_commit_is_the_patch_on_the_pinned_commit_and_is_repeatable(
    source: tuple[Path, str, Path], tmp_path: Path
) -> None:
    root, base, patch = source
    mirror = bare_mirror(root, tmp_path / "mirror.git")

    first = commit_patch(mirror, base, patch, DRAFT)
    second = commit_patch(mirror, base, patch, DRAFT)

    assert first == second
    assert git(mirror, "rev-parse", f"{first}^") == base
    assert git(mirror, "show", f"{first}:README.md") == "Run npm install."
    assert git(mirror, "show", f"{first}:NEW.md") == "added"
    assert git(mirror, "log", "-1", "--format=%an <%ae>|%B", first) == (
        "SecondShift <secondshift@users.noreply.github.com>|"
        "Fix stale setup steps\n\n[skip ci]"
    )


def test_a_patch_that_no_longer_applies_is_refused(
    source: tuple[Path, str, Path], tmp_path: Path
) -> None:
    root, base, patch = source
    mirror = bare_mirror(root, tmp_path / "mirror.git")
    patch.write_text(patch.read_text().replace("-Run npm ci.", "-Something else."))

    with pytest.raises(PublishError, match="no longer apply"):
        commit_patch(mirror, base, patch, DRAFT)


def github(
    monkeypatch: pytest.MonkeyPatch, *responses: httpx.Response
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    monkeypatch.setattr(
        rest, "client", lambda: httpx.Client(transport=httpx.MockTransport(handle))
    )
    return seen


PULL = {"number": 12, "html_url": "https://github.com/acme/web/pull/12"}


def test_a_draft_is_opened_against_the_base_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = github(monkeypatch, httpx.Response(201, json=PULL))

    opened = open_draft("acme/web", "write-token", DRAFT)

    assert opened == pull_requests.PublishedPullRequest(12, PULL["html_url"])
    body = json.loads(seen[0].content)
    assert body == {
        "title": DRAFT.title,
        "head": DRAFT.branch,
        "base": "main",
        "body": DRAFT.body,
        "draft": True,
    }
    assert seen[0].headers["Authorization"] == "Bearer write-token"


def test_a_retry_finds_the_draft_an_earlier_attempt_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = github(
        monkeypatch,
        httpx.Response(422, json={"errors": [{"message": "A pull request exists"}]}),
        httpx.Response(200, json=[PULL]),
    )

    assert open_draft("acme/web", "write-token", DRAFT).number == 12
    assert seen[1].url.params["head"] == "acme:secondshift/run-7"


def test_github_reasons_are_shown_without_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github(
        monkeypatch,
        httpx.Response(
            422,
            json={"errors": [{"message": "Draft pull requests are not supported"}]},
        ),
        httpx.Response(200, json=[]),
    )

    with pytest.raises(PublishError) as raised:
        open_draft("acme/web", "write-token", DRAFT)

    assert "Draft pull requests are not supported" in str(raised.value)
    assert "write-token" not in str(raised.value)


@pytest.fixture
def repository(db: object, settings: Any, tmp_path: Path) -> Repository:
    settings.SOURCE_CACHE_ROOT = tmp_path / "cache"
    owner = get_user_model().objects.create_user(username="owner")
    workspace: Workspace = create_workspace(name="Test", slug="test", owner=owner)
    connection = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.SOURCE,
        auth_strategy=Connection.AuthStrategy.MANAGED,
        display_name="GitHub",
        external_account_id="99",
    )
    return Repository.objects.create(
        workspace=workspace,
        full_name="acme/web",
        external_id="42",
        github_connection=connection,
        allow_pull_requests=True,
    )


def test_publishing_pushes_with_a_write_token_and_opens_the_draft(
    repository: Repository,
    source: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, patch = source
    mirror = bare_mirror(root, mirror_path(repository.pk))
    pushes: list[tuple[str, ...]] = []
    tokens = fake_github(monkeypatch, "acme/web", "write-token")
    monkeypatch.setattr(
        pull_requests,
        "authenticated_git",
        lambda root, token, *args: pushes.append((token, *args)) or "",
    )
    github(monkeypatch, httpx.Response(201, json=PULL))

    opened = publish_pull_request(repository, base, patch, DRAFT)

    assert opened.url == PULL["html_url"]
    # One token, minted with write access, serves the push and the request.
    [mint] = tokens.call_args_list
    assert mint.args[1:] == (42, {"contents": "write", "pull_requests": "write"})
    [push] = pushes
    commit = commit_patch(mirror, base, patch, DRAFT)
    assert push == (
        "write-token",
        "push",
        "--quiet",
        "--",
        "https://github.com/acme/web.git",
        f"{commit}:refs/heads/secondshift/run-7",
    )


def test_missing_write_permission_names_the_fix(
    repository: Repository,
    source: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, base, patch = source
    fake_github(monkeypatch, "acme/web").side_effect = GitHubError(
        "GitHub access failed."
    )

    with pytest.raises(PublishError, match="Contents and Pull requests write"):
        publish_pull_request(repository, base, patch, DRAFT)


def test_a_commit_github_no_longer_has_is_named(
    repository: Repository,
    source: tuple[Path, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, patch = source
    bare_mirror(root, mirror_path(repository.pk))
    fake_github(monkeypatch, "acme/web", "t")

    def gone(*args: Any) -> None:
        raise GitHubError("The commit 0000 is no longer available from GitHub.")

    monkeypatch.setattr(pull_requests, "fetch_github_commit", gone)

    with pytest.raises(PublishError, match="no longer available"):
        publish_pull_request(repository, "0" * 40, patch, DRAFT)
