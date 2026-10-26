from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field


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


class RepositoryValidationSchema(Schema):
    status: Literal["valid", "warning", "error"]
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


class RepositoryDetailSchema(RepositorySchema):
    config_path: str
    # The repository's choice, and whether this server syncs in the background.
    auto_sync: bool
    server_syncs: bool
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
