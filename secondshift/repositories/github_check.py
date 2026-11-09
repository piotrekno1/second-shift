"""Check what a GitHub source connection can still do, feature by feature.

The result is kept as the connection's last check, so the dashboard shows
each gap with a link to fix it on GitHub. Tokens are never part of it.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from secondshift.config.constants import GITHUB_BASE_URL
from secondshift.connections.checks import record_check
from secondshift.connections.dao import connected_github_sources
from secondshift.connections.github import can_check, connection_source
from secondshift.connections.models import Connection
from secondshift.github import app, credentials, permissions
from secondshift.github.errors import GitHubError
from secondshift.github.tokens import inspect_token
from secondshift.repositories.dao import connection_repositories
from secondshift.repositories.github_access import (
    GitHubAccess,
    github_access,
)
from secondshift.repositories.models import Repository

# A grant can be withdrawn on GitHub at any time, so each source is checked daily.
CHECK_INTERVAL = timedelta(days=1)

GITHUB_TOKENS_URL = f"{GITHUB_BASE_URL}/settings/personal-access-tokens"

PERMISSION_LABELS = {
    "contents": "Contents",
    "pull_requests": "Pull requests",
    "actions": "Actions",
    "statuses": "Commit statuses",
    "issues": "Issues",
}


@dataclass(frozen=True)
class FeatureCheck:
    feature: str
    label: str
    # None when it cannot be checked without using it.
    ok: bool | None
    message: str


@dataclass(frozen=True)
class SourceCheck:
    ok: bool
    message: str
    features: list[FeatureCheck]
    fix_url: str = ""


def names(permission_names: list[str]) -> str:
    return ", ".join(PERMISSION_LABELS[name] for name in permission_names)


def features(
    reads: dict[str, bool] | None, writes: dict[str, bool] | None
) -> list[FeatureCheck]:
    """One line per feature. reads is None when no repository could be probed."""
    if reads is None:
        unknown = "Add a repository to check this."
        return [
            FeatureCheck("sync", "Sync", None, unknown),
            FeatureCheck("tools", "GitHub tools", None, unknown),
            FeatureCheck("draft_pull_requests", "Draft pull requests", None, unknown),
            FeatureCheck("issues", "Issues", None, unknown),
        ]
    missing_tools = [name for name in permissions.TOOLS if not reads[name]]
    sync = (
        FeatureCheck("sync", "Sync", True, "Can read code.")
        if reads["contents"]
        else FeatureCheck(
            "sync", "Sync", False, "Needs read access to Contents. Sync fails."
        )
    )
    tools = (
        FeatureCheck(
            "tools",
            "GitHub tools",
            True,
            "Can read pull requests, Actions runs and commit statuses.",
        )
        if not missing_tools
        else FeatureCheck(
            "tools",
            "GitHub tools",
            False,
            f"Needs read access to {names(missing_tools)}.",
        )
    )
    drafts = write_feature(
        writes,
        "draft_pull_requests",
        "Draft pull requests",
        permissions.DRAFT_PULL_REQUESTS,
        "Can push a branch and open a draft pull request.",
        "a draft pull request opens",
    )
    issues = write_feature(
        writes,
        "issues",
        "Issues",
        permissions.ISSUES,
        "Can open issues.",
        "an issue opens",
    )
    return [sync, tools, drafts, issues]


def write_feature(
    writes: dict[str, bool] | None,
    feature: str,
    label: str,
    needed: tuple[str, ...],
    ready: str,
    first_use: str,
) -> FeatureCheck:
    """A feature that writes. A token's write access is only known on use."""
    if writes is None:
        return FeatureCheck(
            feature,
            label,
            None,
            f"A token's write access is checked when {first_use}.",
        )
    missing = [name for name in needed if not writes[name]]
    if missing:
        return FeatureCheck(
            feature, label, False, f"Needs write access to {names(missing)}."
        )
    return FeatureCheck(feature, label, True, ready)


def reach(repositories: list[Repository]) -> tuple[GitHubAccess | None, list[str]]:
    """The first repository reached, and the names of those that were not."""
    first: GitHubAccess | None = None
    unreachable: list[str] = []
    for repository in repositories:
        try:
            access = github_access(repository)
        except GitHubError:
            unreachable.append(repository.full_name)
            continue
        first = first or access
    return first, unreachable


def summary(
    repositories: list[Repository], unreachable: list[str], checked: list[FeatureCheck]
) -> tuple[bool, str]:
    if unreachable:
        return False, (
            f"Cannot reach {', '.join(unreachable)}. "
            "Give this connection access to them on GitHub."
        )
    if checked[0].ok is False:
        return False, "Cannot read code, so sync fails."
    if not repositories:
        return True, "Connected. No repositories added yet."
    count = len(repositories)
    return (
        True,
        f"Reaches {'its repository' if count == 1 else f'all {count} repositories'}.",
    )


def check_token(
    connection: Connection,
    source: credentials.PersonalToken,
    repositories: list[Repository],
) -> SourceCheck:
    owner = inspect_token(source.token)
    if connection.expires_at != owner.expires_at:
        connection.expires_at = owner.expires_at
        connection.save(update_fields=["expires_at", "updated_at"])
    first, unreachable = reach(repositories)
    reads = (
        permissions.token_reads(
            source.token,
            first.full_name,
            str(first.metadata.get("default_branch") or "HEAD"),
            permissions.SYNC + permissions.TOOLS,
        )
        if first
        else None
    )
    checked = features(reads, None)
    ok, message = summary(repositories, unreachable, checked)
    return SourceCheck(ok, message, checked, GITHUB_TOKENS_URL)


def check_installation(
    source: credentials.AppInstallation, repositories: list[Repository]
) -> SourceCheck:
    try:
        details = app.installation(source.installation_id)
    except GitHubError:
        return SourceCheck(
            False,
            "GitHub could not find this App installation. It may have been removed.",
            [],
        )
    granted = details.permissions
    # An App's grants hold for every repository, so no repository is probed.
    unreachable = reach(repositories)[1]
    checked = features(
        permissions.granted_reads(granted, permissions.SYNC + permissions.TOOLS),
        permissions.granted_writes(
            granted, permissions.DRAFT_PULL_REQUESTS + permissions.ISSUES
        ),
    )
    ok, message = summary(repositories, unreachable, checked)
    return SourceCheck(ok, message, checked, details.settings_url)


def check_source(connection: Connection) -> SourceCheck:
    """Check the credential, every added repository, and each feature's access."""
    repositories = connection_repositories(connection)
    try:
        source = connection_source(connection)
        if isinstance(source, credentials.AppInstallation) and not app.configured():
            # Without the App's key, no installation token can be made.
            raise GitHubError(
                "This server has no GitHub App configured, so this connection "
                "cannot sync. Ask the server's operator to configure the App, "
                "or connect a personal access token instead."
            )
        if isinstance(source, credentials.PersonalToken):
            result = check_token(connection, source, repositories)
        else:
            result = check_installation(source, repositories)
    except GitHubError as error:
        # These messages never contain a token.
        token = connection.auth_strategy == Connection.AuthStrategy.API_KEY
        result = SourceCheck(False, str(error), [], GITHUB_TOKENS_URL if token else "")
    record_check(
        connection,
        result.ok,
        result.message,
        features=[asdict(feature) for feature in result.features],
        fix_url=result.fix_url,
    )
    return result


def last_checked(connection: Connection) -> datetime | None:
    check = connection.metadata.get("last_check") or {}
    try:
        return datetime.fromisoformat(check["checked_at"])
    except (KeyError, TypeError, ValueError):
        return None


def check_due_sources() -> int:
    """Check each GitHub source not checked within a day. Returns how many."""
    due = timezone.now() - CHECK_INTERVAL
    checked = 0
    for connection in connected_github_sources():
        when = last_checked(connection)
        if not can_check(connection) or (when is not None and when > due):
            continue
        check_source(connection)
        checked += 1
    return checked


def check_after_failure(connection: Connection | None) -> None:
    """A sync failed, so show why on the connection, unless it already shows a failure."""
    if not can_check(connection):
        return
    if (connection.metadata.get("last_check") or {}).get("ok") is False:
        return
    check_source(connection)
