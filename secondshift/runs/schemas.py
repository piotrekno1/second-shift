from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field

from secondshift.config.constants import RunStart
from secondshift.runs.models import Run
from secondshift.runs.reporting.queries import Period, RunSort


class RunFailureSchema(Schema):
    label: str
    reason: str
    # The run stopped at one of its limits, which an owner can change.
    limit: bool = False
    # The connection to fix, when the failure came from one, and what to do.
    connection_id: int | None = None
    next_step: str = ""


class RunSchema(Schema):
    id: int
    job_id: int | None
    job: str
    repository_id: int
    repository: str
    status: str
    scheduled_for: datetime
    # schedule, manual, webhook or mcp.
    started_by: RunStart
    started_at: datetime | None = None
    finished_at: datetime | None
    summary: str
    plan_ready: bool
    commit_sha: str = ""
    model: str | None = None
    max_turns: int | None = None
    token_budget: int | None = None
    model_requests: int = 0
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    # Cached input. Already inside model_input_tokens.
    model_cache_read_tokens: int = 0
    failure: RunFailureSchema | None = None
    has_input: bool = False
    has_output: bool = False
    model_tokens: int | None = 0
    # What counts toward token_budget: cached input counts as a tenth.
    model_budget_tokens: int | None = 0
    model_usage_source: Literal["api", "agent"] = "api"
    model_cost_usd: float | None = None
    model_cost_partial: bool = False


class RunEventSchema(Schema):
    id: int
    level: str
    message: str
    created_at: datetime


RunFileName = Literal["input", "output"]


class RunFileSchema(Schema):
    name: RunFileName
    label: str
    filename: str
    size_bytes: int | None
    truncated: bool


class RunDeliverySchema(Schema):
    id: int
    kind: str
    target: str
    status: str
    attempts: int
    message: str
    # Where the result lives, such as the draft pull request. Empty if none.
    url: str
    updated_at: datetime
    # Failed, and it can still be sent: a pull request, or a connection that exists.
    retryable: bool


class RunIssueSchema(Schema):
    title: str
    # Empty until the issue opens on GitHub.
    number: int | None
    url: str
    # An open issue already had this title, so none was opened.
    already_open: bool


class RunChangesSchema(Schema):
    file_count: int
    # The first changed paths. The patch holds all of them.
    files: list[str]
    too_large: bool
    # False when the changes were not kept, for example past the size limit.
    available: bool
    size_bytes: int | None = None


class RunSavedFileSchema(Schema):
    name: str
    size_bytes: int


class RunSkippedFileSchema(Schema):
    name: str
    reason: str


class RunDetailSchema(RunSchema):
    submission_input: str = ""
    job_key: str
    skill_path: str
    created_at: datetime
    exit_code: int | None
    model_provider: str | None
    execution_agent: str | None = None
    output_excerpt: str
    output_truncated: bool
    output_size_bytes: int | None
    events: list[RunEventSchema]
    files: list[RunFileSchema]
    deliveries: list[RunDeliverySchema] = []
    # Issues the agent proposed, opened after the run if allowed.
    issues: list[RunIssueSchema] = []
    # What the sandbox left behind. None when nothing was collected.
    changes: RunChangesSchema | None = None
    saved_files: list[RunSavedFileSchema] = []
    skipped_files: list[RunSkippedFileSchema] = []


class RunListQuery(Schema):
    q: str = Field("", max_length=200)
    status: list[Run.Status] = []
    job_id: int | None = None
    repository_id: int | None = None
    period: Period | None = None
    sort: RunSort = "newest"
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=100)


class RunPageSchema(Schema):
    items: list[RunSchema]
    count: int
    page: int
    page_size: int
    status_counts: dict[str, int]


class RunInProgressSchema(Schema):
    detail: str
    # The run that has not finished yet, so the client can follow it.
    run_id: int


class TaskSubmissionSchema(Schema):
    task_id: int
    run: RunSchema
    started: bool
