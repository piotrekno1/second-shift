from ninja import Schema
from pydantic import ConfigDict, Field


class StrictSchema(Schema):
    model_config = ConfigDict(extra="forbid")


class SessionInput(StrictSchema):
    run_id: int = Field(gt=0)
    session_key: str = Field(min_length=32, max_length=32)
    repository: str = Field(min_length=3, max_length=300)
    commit_sha: str = Field(min_length=40, max_length=64)
    image: str = Field(min_length=1, max_length=300)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SessionOutput(Schema):
    session_id: str


class CommandInput(StrictSchema):
    call_id: str = Field(min_length=1, max_length=200)
    argv: list[str] = Field(min_length=1, max_length=256)
    cwd: str = Field(default=".", max_length=1000)
    timeout_seconds: int = Field(default=120, ge=1, le=900)


class CommandOutput(Schema):
    call_id: str
    exit_code: int | None
    output: str
    timed_out: bool
