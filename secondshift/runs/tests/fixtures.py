import subprocess
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User

from secondshift.connections.execution import choose_execution
from secondshift.connections.models import Connection
from secondshift.repositories.local import git
from secondshift.repositories.models import Repository
from secondshift.workspaces.dao import create_workspace

SKILL = ".agents/skills/review/SKILL.md"


@pytest.fixture
def owner() -> User:
    return get_user_model().objects.create_user(username="owner")


@pytest.fixture
def repository(owner: User, tmp_path: Path, settings: Any) -> Repository:
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / SKILL).parent.mkdir(parents=True)
    (root / SKILL).write_text("# Review\nWrite a useful report.")
    git(root, "add", ".")
    git(root, "commit", "-m", "Add skill")
    workspace = create_workspace("Test", "test", owner)
    model = Connection.objects.create(
        workspace=workspace,
        provider=Connection.Provider.ANTHROPIC,
        kind=Connection.Kind.MODEL,
        display_name="Anthropic",
        default_model="claude-opus-5-5",
    )
    choose_execution(workspace, model=model)
    settings.LOCAL_REPOSITORIES_ENABLED = True
    settings.SOURCE_CACHE_ROOT = tmp_path / "source-cache"
    return Repository.objects.create(
        workspace=workspace,
        source_type=Repository.SourceType.LOCAL,
        local_path=str(root),
        full_name="local/project",
        default_branch="main",
        enabled=True,
    )


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    ids: list[int] = []
    monkeypatch.setattr(
        "secondshift.runs.submissions.enqueue_run", lambda run: ids.append(run.pk)
    )
    return ids
