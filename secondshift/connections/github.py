"""GitHub source connections: how a workspace reaches its repositories."""

from secondshift.connections.models import Connection
from secondshift.github.installations import Installation
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
