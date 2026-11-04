"""GitHub source connections: how a workspace reaches its repositories."""

from secondshift.connections.models import Connection
from secondshift.github.errors import GitHubError
from secondshift.github.installations import Installation
from secondshift.github.tokens import inspect_token
from secondshift.workspaces.models import Workspace


def link_installations(
    workspace: Workspace, installations: list[Installation]
) -> list[Connection]:
    """Add or reconnect one source connection per App installation."""
    return [
        Connection.objects.update_or_create(
            workspace=workspace,
            provider=Connection.Provider.GITHUB,
            external_account_id=installation.installation_id,
            defaults={
                "kind": Connection.Kind.SOURCE,
                "auth_strategy": Connection.AuthStrategy.MANAGED,
                "display_name": f"GitHub · {installation.account}",
                "status": Connection.Status.CONNECTED,
            },
        )[0]
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
    connection, _ = Connection.objects.update_or_create(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        external_account_id=account_id,
        defaults={
            "kind": Connection.Kind.SOURCE,
            "auth_strategy": Connection.AuthStrategy.API_KEY,
            "display_name": f"GitHub · {owner.login}",
            "access_token": token.strip(),
            "expires_at": owner.expires_at,
            "status": Connection.Status.CONNECTED,
        },
    )
    return connection
