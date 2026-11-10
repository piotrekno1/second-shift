import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from secondshift.repositories import skill_source
from secondshift.repositories.github_access import GitHubAccess
from secondshift.repositories.local import LocalRepositoryError
from secondshift.repositories.models import Repository
from secondshift.repositories.tests.test_local import run_git


@dataclass(frozen=True)
class LocalSourceAccess(GitHubAccess):
    """GitHub access whose clone URL is a local repository."""

    source: str = ""

    @property
    def clone_url(self) -> str:
        return self.source


def head(path: Path, ref: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", ref], text=True
    ).strip()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A repository whose default branch is stable, with a feature branch."""
    path = tmp_path / "source"
    path.mkdir()
    run_git(path, "init", "-b", "stable")
    run_git(path, "config", "user.email", "test@secondshift.local")
    run_git(path, "config", "user.name", "SecondShift Test")
    skill = path / "skills/review/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Stable\n")
    run_git(path, "add", ".")
    run_git(path, "commit", "-m", "stable")
    run_git(path, "switch", "-c", "feature/review")
    skill.write_text("# Feature\n")
    run_git(path, "commit", "-am", "feature")
    run_git(path, "switch", "stable")
    return path


@pytest.mark.parametrize("revision", [None, "feature/review", "commit"])
def test_github_submission_resolves_the_requested_source(
    revision: str | None,
    source: Path,
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.SOURCE_CACHE_ROOT = tmp_path / "sources"
    monkeypatch.setattr(
        skill_source,
        "github_access",
        lambda repo: LocalSourceAccess(
            42, repo.full_name, "server-token", {}, str(source)
        ),
    )
    repository = Repository(
        pk=12,
        source_type=Repository.SourceType.GITHUB,
        full_name="example/project",
        default_branch="stable",
    )
    if revision == "commit":
        revision = head(source, "feature/review")
    expected = head(source, revision or "stable")

    assert (
        skill_source.skill_commit(repository, "skills/review/SKILL.md", revision)
        == expected
    )
    # Source selection never updates synced configuration or reconciles jobs.
    assert repository.config_sha == ""


def test_github_submission_refuses_a_skill_missing_at_that_commit(
    source: Path, tmp_path: Path, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.SOURCE_CACHE_ROOT = tmp_path / "sources"
    monkeypatch.setattr(
        skill_source,
        "github_access",
        lambda repo: LocalSourceAccess(
            42, repo.full_name, "server-token", {}, str(source)
        ),
    )
    repository = Repository(
        pk=12, source_type=Repository.SourceType.GITHUB, default_branch="stable"
    )
    with pytest.raises(LocalRepositoryError, match="not committed"):
        skill_source.skill_commit(repository, "skills/missing/SKILL.md", None)


@pytest.mark.parametrize("revision", ["HEAD~1", "main:file", "../private"])
def test_git_expressions_are_rejected_before_remote_access(
    revision: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = Mock()
    monkeypatch.setattr(skill_source, "github_access", remote)
    repository = Repository(source_type=Repository.SourceType.GITHUB)
    with pytest.raises(LocalRepositoryError):
        skill_source.skill_commit(repository, "skills/review/SKILL.md", revision)
    remote.assert_not_called()
