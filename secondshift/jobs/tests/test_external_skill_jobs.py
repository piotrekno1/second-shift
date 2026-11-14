"""Jobs whose skill is pinned in another repository."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from secondshift.jobs.models import Job
from secondshift.jobs.planning import build_execution_plan
from secondshift.jobs.repository_sync import attach_local_repository
from secondshift.repositories.tests.conftest import (
    workspace as workspace_fixture,
)
from secondshift.repositories.tests.test_external_skills import (
    PATH,
    REPOSITORY,
)
from secondshift.repositories.tests.test_external_skills import (
    vendor as vendor_fixture,
)
from secondshift.repositories.tests.test_local import run_git
from secondshift.workspaces.models import Workspace

pytestmark = pytest.mark.django_db
vendor = vendor_fixture
workspace = workspace_fixture


def head(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def project(tmp_path: Path, ref: str) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.email", "test@secondshift.local")
    run_git(root, "config", "user.name", "SecondShift Test")
    (root / ".secondshift.yaml").write_text(
        f"""
version: 1
jobs:
  triage:
    name: Triage
    skill: {PATH}
    skill_source:
      repository: {REPOSITORY}
      ref: {ref}
""".strip()
    )
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "Add SecondShift configuration")
    return root


@pytest.fixture(autouse=True)
def local_repositories(settings: Any) -> None:
    settings.LOCAL_REPOSITORIES_ENABLED = True


def test_a_pinned_skill_syncs_and_plans(
    workspace: Workspace, vendor: Path, tmp_path: Path
) -> None:
    commit = head(vendor)

    repository = attach_local_repository(workspace, str(project(tmp_path, commit)))

    assert repository.last_sync_error == ""
    job = Job.objects.get(repository=repository)
    assert job.skill_path == PATH
    assert job.skill_source == {"repository": REPOSITORY, "ref": commit}
    assert job.skill_text.startswith("---\nname: triage")
    plan = build_execution_plan(job)
    assert plan.job.skill_source is not None
    assert plan.job.skill_source.commit_sha == commit
    assert plan.job.sandbox_skill_path == "/skill/SKILL.md"


def test_a_branch_is_refused_so_nothing_unreviewed_runs(
    workspace: Workspace, vendor: Path, tmp_path: Path
) -> None:
    repository = attach_local_repository(workspace, str(project(tmp_path, "main")))

    assert "skill_source.ref must be a full commit SHA" in repository.last_sync_error
    assert not Job.objects.exists()


def test_a_missing_skill_is_a_sync_error(
    workspace: Workspace, vendor: Path, tmp_path: Path
) -> None:
    repository = attach_local_repository(workspace, str(project(tmp_path, "b" * 40)))

    assert "Could not fetch" in repository.last_sync_error
    assert not Job.objects.exists()
