"""GitHub source connections: how a workspace reaches its repositories."""

from secondshift.connections.dao import save_github_source
from secondshift.connections.models import Connection
from secondshift.github import credentials
from secondshift.github.app import configured
from secondshift.github.errors import GitHubError
from secondshift.github.installations import Installation
from secondshift.github.tokens import inspect_token
from secondshift.workspaces.models import Workspace


def link_installations(
    workspace: Workspace, installations: list[Installation]
) -> list[Connection]:
    """Add or reconnect one source connection per App installation."""
    return [
        save_github_source(
            workspace,
            installation.installation_id,
            {
                "kind": Connection.Kind.SOURCE,
                "auth_strategy": Connection.AuthStrategy.MANAGED,
                "display_name": f"GitHub · {installation.account}",
                "status": Connection.Status.CONNECTED,
            },
        )
        for installation in installations
    ]


# Keeps token connections apart from installations, whose IDs are numbers.
TOKEN_ACCOUNT_PREFIX = "token:"


def connect_token_source(
    workspace: Workspace, token: str, replacing: Connection | None = None
) -> Connection:
    """Add a personal access token as a source, or replace the owner's last one.

    GitHub checks the token first. A token for the same GitHub account
    replaces the stored one, so its repositories keep working. When
    replacing a given connection, the token must belong to its account.
    """
    owner = inspect_token(token.strip())
    account_id = f"{TOKEN_ACCOUNT_PREFIX}{owner.login}"
    if replacing is not None and replacing.external_account_id != account_id:
        expected = replacing.external_account_id.removeprefix(TOKEN_ACCOUNT_PREFIX)
        raise GitHubError(
            f"This token belongs to {owner.login}. Use a token from {expected}."
        )
    return save_github_source(
        workspace,
        account_id,
        {
            "kind": Connection.Kind.SOURCE,
            "auth_strategy": Connection.AuthStrategy.API_KEY,
            "display_name": f"GitHub · {owner.login}",
            "access_token": token.strip(),
            "expires_at": owner.expires_at,
            "status": Connection.Status.CONNECTED,
        },
    )


def usable_source(connection: Connection | None) -> bool:
    if not (
        connection
        and connection.provider == Connection.Provider.GITHUB
        and connection.kind == Connection.Kind.SOURCE
        and connection.status == Connection.Status.CONNECTED
    ):
        return False
    if connection.auth_strategy == Connection.AuthStrategy.MANAGED:
        return True
    return connection.auth_strategy == Connection.AuthStrategy.API_KEY and bool(
        connection.access_token
    )


def can_check(connection: Connection | None) -> bool:
    """App sources need the App on this server. Token sources need only themselves."""
    return usable_source(connection) and (
        connection.auth_strategy != Connection.AuthStrategy.MANAGED or configured()
    )


def connection_source(connection: Connection | None) -> credentials.Source:
    """The credential behind a source connection. Never returned to a client."""
    if not usable_source(connection):
        raise GitHubError("The GitHub connection is disconnected.")
    if connection.auth_strategy == Connection.AuthStrategy.MANAGED:
        return credentials.AppInstallation(connection.external_account_id)
    if connection.current_status == Connection.Status.EXPIRED:
        raise GitHubError("The GitHub token expired. Enter a new one.")
    return credentials.PersonalToken(connection.access_token)
