"""One way to get a GitHub token, whatever kind of credential stands behind it."""

from dataclasses import dataclass

from secondshift.github import app

READ = {"contents": "read"}
METADATA_READ = {"metadata": "read"}
WRITE = {"contents": "write", "pull_requests": "write"}
ISSUES_WRITE = {"issues": "write"}


@dataclass(frozen=True)
class AppInstallation:
    """A GitHub App installation. Each token is minted for one request."""

    installation_id: str


@dataclass(frozen=True)
class PersonalToken:
    """A fine-grained personal access token. It cannot be narrowed per request."""

    token: str


Source = AppInstallation | PersonalToken


def token_for(
    source: Source, repository_id: int | None, permissions: dict[str, str]
) -> str:
    """A token for one repository, or for the whole source when repository_id is None.

    App tokens carry only the permissions asked for and expire within an hour.
    A personal token is returned as it is. GitHub refuses what it cannot do.
    """
    if isinstance(source, PersonalToken):
        return source.token
    return app.installation_token(source.installation_id, repository_id, permissions)


def read_token_for(
    source: Source, repository_id: int | None, wanted: tuple[str, ...]
) -> str:
    """A read-only token with the wanted permissions the source has.

    An App token never asks for more than the installation granted, so a
    missing permission fails only the calls that need it. A personal token
    has whatever its owner gave it.
    """
    if isinstance(source, PersonalToken):
        return source.token
    granted = app.installation_permissions(source.installation_id)
    permissions = {
        name: "read" for name in wanted if granted.get(name) in {"read", "write"}
    }
    return app.installation_token(
        source.installation_id, repository_id, permissions or METADATA_READ
    )
