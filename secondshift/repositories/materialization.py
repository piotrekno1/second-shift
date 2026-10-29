import subprocess
from pathlib import Path


class RepositoryMaterializationError(RuntimeError):
    pass


def git_in(repository: Path, *arguments: str) -> str:
    return run_git("-C", str(repository), *arguments)


def run_git(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise RepositoryMaterializationError(
            "Git timed out while preparing the runner repository."
        ) from error
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RepositoryMaterializationError(
            detail or "Git could not prepare the runner repository."
        )
    return completed.stdout.strip()
