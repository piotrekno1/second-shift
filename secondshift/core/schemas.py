from datetime import datetime

from ninja import Schema


class UserSchema(Schema):
    email: str
    workspace_name: str | None
    workspace_slug: str | None
    # The person's role in the current workspace and what it lets them do.
    role: str | None
    permissions: list[str]
    # Django admin only lets staff in, so links to it are shown only to them.
    can_use_admin: bool
    can_create_workspace: bool
    local_repositories_enabled: bool
    environment: str = "Production"
    sandbox: str = "gVisor sandbox"


class FailingJobSchema(Schema):
    job_id: int
    job: str
    repository: str
    run_id: int
    failed_at: datetime | None
    label: str
    reason: str


class JobSpendSchema(Schema):
    job_id: int
    job: str
    repository: str
    runs: int
    spend_usd: float
    partial: bool


class OverviewSchema(Schema):
    repositories: int
    local_repositories: int
    enabled_jobs: int
    runs_today: int
    failed_runs: int
    connected_services: int
    failing_jobs: list[FailingJobSchema] = []
    runs_7d: int = 0
    finished_7d: int = 0
    succeeded_7d: int = 0
    spend_7d_usd: float = 0
    spend_previous_7d_usd: float = 0
    spend_partial: bool = False
    spend_by_job: list[JobSpendSchema] = []
