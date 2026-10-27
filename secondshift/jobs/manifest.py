import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from croniter import croniter
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)
    tools: list[str] = Field(min_length=1)
    required: bool = True

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("tool names cannot be empty")
        if len(values) != len(set(values)):
            raise ValueError("tool names must be unique within a binding")
        return values


class RequirementsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcps: list[CapabilityManifest] = Field(default_factory=list)
    apis: list[CapabilityManifest] = Field(default_factory=list)


class NotificationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)
    required: bool = True


class JobManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    skill: str = Field(min_length=1, max_length=500)
    # Without a schedule, the job runs only when started with Run now.
    schedule: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str = "UTC"
    # How many of the job's own earlier reports each run starts with.
    history: int = Field(default=0, ge=0)
    requires: RequirementsManifest = Field(default_factory=RequirementsManifest)
    notify: list[NotificationManifest] = Field(default_factory=list)

    @field_validator("skill")
    @classmethod
    def validate_skill_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("skill must be a repository-relative path")
        if path.name != "SKILL.md":
            raise ValueError("skill must point to a SKILL.md file")
        return value

    @field_validator("history")
    @classmethod
    def validate_history(cls, value: int) -> int:
        if value > settings.JOB_HISTORY_MAX:
            raise ValueError(
                f"history cannot be above {settings.JOB_HISTORY_MAX} on this server"
            )
        return value

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, value: str | None) -> str | None:
        if value is not None and not croniter.is_valid(value):
            raise ValueError("schedule must be a valid five-field cron expression")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be an IANA timezone") from error
        return value

    @model_validator(mode="after")
    def validate_unique_bindings(self) -> Self:
        bindings = [
            item.binding
            for item in [*self.requires.mcps, *self.requires.apis, *self.notify]
        ]
        if len(bindings) != len(set(bindings)):
            raise ValueError("bindings must be unique within a job")
        return self


class RepositoryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    jobs: dict[str, JobManifest]

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only manifest version 1 is supported")
        return value


def load_manifest(path: Path) -> RepositoryManifest:
    return parse_manifest(path.read_text())


def parse_manifest(text: str) -> RepositoryManifest:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("manifest root must be a mapping")
    return RepositoryManifest.model_validate(raw)


def manifest_hash(job: JobManifest) -> str:
    canonical = json.dumps(job.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
