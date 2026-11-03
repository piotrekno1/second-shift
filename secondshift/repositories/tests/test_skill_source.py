from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from secondshift.connections.models import Connection
from secondshift.repositories import skill_source
from secondshift.repositories.github_access import GitHubAccess
from secondshift.repositories.local import LocalRepositoryError
from secondshift.repositories.models import Repository


@pytest.mark.parametrize("revision", [None, "feature/review", "a" * 40, "a" * 64])
def test_github_submission_fetches_requested_source_and_verifies_the_pinned_skill(
    revision: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Repository(
        pk=12,
        source_type=Repository.SourceType.GITHUB,
        full_name="example/project",
        default_branch="stable",
        external_id="42",
        github_connection=Connection(external_account_id="7"),
    )
    commit = "a" * (64 if revision and len(revision) == 64 else 40)
    fetch = Mock(side_effect=["", commit])
    read = Mock(return_value="# Skill")
    monkeypatch.setattr(
        skill_source,
        "github_access",
        lambda repo: GitHubAccess(42, repo.full_name, "server-token", {}),
    )
    monkeypatch.setattr(skill_source, "locked_mirror", lambda pk: nullcontext(tmp_path))
    monkeypatch.setattr(skill_source, "authenticated_git", fetch)
    monkeypatch.setattr(skill_source, "committed_file", read)

    assert (
        skill_source.skill_commit(repository, "skills/review/SKILL.md", revision)
        == commit
    )

    target = (
        revision
        if revision and len(revision) in (40, 64)
        else f"refs/heads/{revision or 'stable'}"
    )
    assert fetch.call_args_list[0].args[-2:] == (
        "https://github.com/example/project.git",
        target,
    )
    assert "--" in fetch.call_args_list[0].args
    read.assert_called_once_with(tmp_path, commit, "skills/review/SKILL.md")
    # Source selection never updates synced configuration or reconciles jobs.
    assert repository.config_sha == ""


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
