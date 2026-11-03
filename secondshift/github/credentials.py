"""One way to get a GitHub token, whatever kind of credential stands behind it."""

from dataclasses import dataclass

from secondshift.github import app

READ = {"contents": "read"}
WRITE = {"contents": "write", "pull_requests": "write"}


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
