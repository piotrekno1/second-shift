from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from secondshift.github import git
from secondshift.github.errors import GitHubError


def test_git_failures_never_expose_credentials(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        git.subprocess,
        "run",
        lambda *args, **kwargs: Mock(returncode=1, stderr="github-secret"),
    )
    with pytest.raises(GitHubError) as caught:
        git.authenticated_git(tmp_path, "github-secret", "fetch")
    assert "github-secret" not in str(caught.value)
