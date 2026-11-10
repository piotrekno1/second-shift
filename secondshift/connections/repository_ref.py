from dataclasses import dataclass

from secondshift.connections.models import Connection


@dataclass(frozen=True)
class RepositoryRef:
    """The repository a job works on, as external tools see it.

    The repositories app builds it, so connections never read repository rows.
    """

    workspace_id: int
    # owner/repo on GitHub. None when the repository has no GitHub identity.
    github_name: str | None
    # GitHub's id, only for repositories added from GitHub.
    github_id: int | None = None
    # The source connection a GitHub repository was added with.
    source: Connection | None = None
