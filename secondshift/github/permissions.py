"""What a GitHub credential may do, by feature.

An App installation lists its permissions. A personal token does not, so each
read permission is probed with one cheap request. Write access cannot be
probed without writing, so it is never reported for a token.
"""

from urllib.parse import quote

from secondshift.config.constants import GITHUB_API_URL
from secondshift.github.rest import send

# The feature each permission serves, as GitHub names them.
SYNC = ("contents",)
TOOLS = ("pull_requests", "actions", "statuses")
DRAFT_PULL_REQUESTS = ("contents", "pull_requests")


def probe_paths(branch: str) -> dict[str, str]:
    """A read request per permission. Each fails without that permission."""
    return {
        "contents": "commits?per_page=1",
        "pull_requests": "pulls?per_page=1",
        "actions": "actions/runs?per_page=1",
        "statuses": f"commits/{quote(branch, safe='')}/status",
    }


def token_reads(
    token: str, full_name: str, branch: str, names: tuple[str, ...]
) -> dict[str, bool]:
    """Whether the token can read each named permission on this repository."""
    paths = probe_paths(branch)
    return {
        name: send(
            "GET", f"{GITHUB_API_URL}/repos/{full_name}/{paths[name]}", token
        ).status_code
        == 200
        for name in names
    }


def granted_reads(granted: dict[str, str], names: tuple[str, ...]) -> dict[str, bool]:
    return {name: granted.get(name) in {"read", "write"} for name in names}


def granted_writes(granted: dict[str, str], names: tuple[str, ...]) -> dict[str, bool]:
    return {name: granted.get(name) == "write" for name in names}
