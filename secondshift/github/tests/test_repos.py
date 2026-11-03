from unittest.mock import Mock

import pytest

from secondshift.github import repos, rest
from secondshift.github.errors import GitHubError


def test_branches_are_listed_page_by_page(monkeypatch: pytest.MonkeyPatch) -> None:
    request = Mock(
        side_effect=[
            [{"name": f"branch-{index}"} for index in range(100)],
            [{"name": "main"}],
        ]
    )
    monkeypatch.setattr(rest, "request_payload", request)
    branches = repos.branches("token", "owner/project")
    assert len(branches) == 101
    assert branches[-1] == "main"
    assert "/repos/owner/project/branches" in request.call_args.args[1]
    assert "page=2" in request.call_args.args[1]


def test_a_repository_must_keep_its_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repos,
        "request_json",
        lambda *args: {"id": 8, "full_name": "owner/project"},
    )
    with pytest.raises(GitHubError, match="identity changed"):
        repos.repository("token", 7)


def test_a_repository_needs_a_valid_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repos, "request_json", lambda *args: {"id": 7, "full_name": "owner/my project"}
    )
    with pytest.raises(GitHubError, match="Invalid"):
        repos.repository("token", 7)
