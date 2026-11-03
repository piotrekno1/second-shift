"""Stand-ins for GitHub at the token and repository lookup layer."""

from typing import Any
from unittest.mock import Mock

import pytest

from secondshift.github import credentials, repos


def fake_github(
    monkeypatch: pytest.MonkeyPatch,
    full_name: str = "owner/project",
    token: str = "github-secret",
    default_branch: str = "main",
) -> Mock:
    """Answer every token request with token. Returns the mock to count mints."""
    tokens = Mock(return_value=token)
    monkeypatch.setattr(credentials, "token_for", tokens)

    def repository(token: str, repository_id: int) -> dict[str, Any]:
        return {
            "id": repository_id,
            "full_name": full_name,
            "default_branch": default_branch,
        }

    monkeypatch.setattr(repos, "repository", repository)
    return tokens
