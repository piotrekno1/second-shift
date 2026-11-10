from datetime import datetime

from ninja import Schema
from pydantic import Field

from secondshift.repositories.status import ValidationStatus


class RepositorySchema(Schema):
    id: int
    full_name: str
    display_name: str
    default_branch: str
    enabled: bool
    job_count: int
    last_synced_at: datetime | None
    source_type: str
    local_path: str | None
    revision: str


class LocalRepositoryCreateSchema(Schema):
    path: str = Field(min_length=1, max_length=1000)


class LocalRepositoryDirectorySchema(Schema):
    name: str
    path: str


class LocalRepositoryDirectoryListSchema(Schema):
    path: str
    parent: str | None
    directories: list[LocalRepositoryDirectorySchema]


class RepositoryEnabledSchema(Schema):
    enabled: bool


class RepositoryPullRequestsSchema(Schema):
    allow: bool
    run_ci: bool = False


class RepositoryIssuesSchema(Schema):
    allow: bool


class RepositoryValidationSchema(Schema):
    status: ValidationStatus
    message: str
    current_revision: str | None
    synced_revision: str
    needs_sync: bool


class RepositoryJobSchema(Schema):
    id: int
    name: str
    skill_path: str
    cron: str
    timezone: str
    enabled: bool
    grant_status: str
    removed_at: datetime | None = None


class RepositoryDetailSchema(RepositorySchema):
    config_path: str
    # The repository's choice, and whether this server syncs in the background.
    auto_sync: bool
    server_syncs: bool
    # GitHub repositories only. See RepositoryPullRequestsSchema.
    can_open_pull_requests: bool
    allow_pull_requests: bool
    pull_request_ci: bool
    # GitHub repositories only, like pull requests.
    allow_issues: bool
    validation: RepositoryValidationSchema
    jobs: list[RepositoryJobSchema]


class GitHubRepositoryCreateSchema(Schema):
    connection_id: int
    repository_id: int = Field(gt=0)
    branch: str = Field(default="", max_length=255)


class GitHubAvailableRepositorySchema(Schema):
    connection_id: int
    repository_id: int
    full_name: str
    default_branch: str


class GitHubAppStatusSchema(Schema):
    configured: bool
    error: str = ""
    repositories: list[GitHubAvailableRepositorySchema]
