"""Run git against GitHub with a token that never leaves the process."""

import base64
import os
import subprocess
from pathlib import Path

from secondshift.config.constants import GITHUB_BASE_URL
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
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        # No helper may answer a password prompt with stored credentials.
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        **config,
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GitHubError("GitHub repository fetch timed out.") from error
    if result.returncode:
        raise GitHubError("Could not fetch the GitHub repository and branch.")
    return result.stdout.strip()
