import subprocess
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from secondshift.workspaces.dao import create_workspace
from secondshift.workspaces.models import Workspace

REPOSITORY = "vendor/skills"
PATH = "skills/triage/SKILL.md"


def run_git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def workspace(db: object) -> Workspace:
    user = get_user_model().objects.create_user(username="owner")
    return create_workspace(name="Test", slug="test", owner=user)


@pytest.fixture
def vendor(tmp_path: Path, settings: Any) -> Path:
    """A public skills repository, served from a local folder instead of GitHub."""
    work = tmp_path / "work"
    work.mkdir()
    run_git(work, "init", "-b", "main")
    run_git(work, "config", "user.email", "test@secondshift.local")
    run_git(work, "config", "user.name", "SecondShift Test")
    folder = work / "skills/triage"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: triage\nallowed-tools: mcp__linear__create_issue\n---\n# Triage\n"
    )
    (folder / "scripts/collect.py").write_text("print('ok')\n")
    (work / "README.md").write_text("Not part of the skill.\n")
    run_git(work, "add", ".")
    run_git(work, "commit", "-m", "first")
    run_git(work, "tag", "v1")
    served = tmp_path / "github" / "vendor" / "skills.git"
    served.parent.mkdir(parents=True)
    run_git(tmp_path, "clone", "--bare", "--quiet", str(work), str(served))
    # GitHub serves any reachable commit by SHA. Local Git needs this.
    run_git(served, "config", "uploadpack.allowAnySHA1InWant", "true")
    settings.EXTERNAL_SKILLS_BASE_URL = f"file://{tmp_path / 'github'}"
    settings.SOURCE_CACHE_ROOT = tmp_path / "cache"
    settings.EXTERNAL_SKILLS_ENABLED = True
    return work
