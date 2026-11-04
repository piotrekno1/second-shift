"""Repository lookups that work with any token."""

from urllib.parse import quote

from secondshift.config.constants import GITHUB_API_URL
from secondshift.github.errors import GitHubError
from secondshift.github.identity import valid_name
from secondshift.github.rest import paginated, request_json


def repository(token: str, repository_id: int) -> dict:
    """The repository with this ID. Its name may change, its ID never does."""
    data = request_json("GET", f"{GITHUB_API_URL}/repositories/{repository_id}", token)
    if data.get("id") != repository_id:
        raise GitHubError("GitHub repository identity changed.")
    if not valid_name(str(data.get("full_name", ""))):
        raise GitHubError("Invalid GitHub repository name.")
    return data


def branch_head(token: str, repository_id: int, branch: str) -> str:
    data = request_json(
        "GET",
        f"{GITHUB_API_URL}/repositories/{repository_id}/commits/{quote(branch, safe='')}",
        token,
    )
    return data["sha"]


def branches(token: str, full_name: str) -> list[str]:
    batch = paginated(
        f"{GITHUB_API_URL}/repos/{full_name}/branches",
        token,
        None,
        "This repository has too many branches to list.",
    )
    if any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str)
        for item in batch
    ):
        raise GitHubError("GitHub returned an invalid branch listing.")
    return [item["name"] for item in batch]


def token_repositories(token: str) -> list[dict]:
    """The repositories a personal access token was given."""
    return paginated(
        f"{GITHUB_API_URL}/user/repos",
        token,
        None,
        "This token reaches too many repositories to list.",
    )
