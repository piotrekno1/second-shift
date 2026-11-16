"""Run git against GitHub with a token that never leaves the process."""

import base64
from pathlib import Path

from secondshift.config.constants import GITHUB_BASE_URL
from secondshift.git import commands
from secondshift.git.commands import GitError
from secondshift.github.errors import GitHubError


def authenticated_git(root: Path, token: str, *arguments: str) -> str:
    """Run one git command in root, sending the token only as a header."""
    # Never put credentials in URLs, arguments, persisted config, or errors.
    authorization = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return run_git(
        root,
        arguments,
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.{GITHUB_BASE_URL}/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {authorization}",
        },
    )


def public_git(root: Path, *arguments: str) -> str:
    """Run one git command in root without any credentials."""
    return run_git(root, arguments, {"GIT_CONFIG_COUNT": "0"})


def run_git(root: Path, arguments: tuple[str, ...], config: dict[str, str]) -> str:
    try:
        return commands.output(root, *arguments, extra_environment=config)
    except GitError as error:
        if error.timed_out:
            raise GitHubError("GitHub repository fetch timed out.") from error
        raise GitHubError(
            "Could not fetch the GitHub repository and branch."
        ) from error
