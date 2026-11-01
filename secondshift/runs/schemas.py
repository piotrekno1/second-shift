from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field

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
    job_id: int
    job: str
    repository_id: int
    repository: str
    status: str
    scheduled_for: datetime
    # schedule, manual or webhook. Blank for older runs.
    started_by: str = ""
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
    failure: RunFailureSchema | None = None
    has_input: bool = False
    has_output: bool = False
    model_tokens: int = 0
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


class RunDetailSchema(RunSchema):
    job_key: str
    skill_path: str
    created_at: datetime
    exit_code: int | None
    model_provider: str | None
    output_excerpt: str
    output_truncated: bool
    output_size_bytes: int | None
    events: list[RunEventSchema]
    files: list[RunFileSchema]


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
