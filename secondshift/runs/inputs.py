"""What a run was given beyond its plan, as stored in Run.input_snapshot."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class Payload(Record):
    """The stored request body that started a triggered run."""

    path: str
    size_bytes: int
    kept_bytes: int
    truncated: bool


class HistoryFile(Record):
    """An earlier run's saved file, where the sandbox shows it."""

    path: str
    size_bytes: int


class HistoryFiles(Record):
    files: tuple[HistoryFile, ...] = ()
    # How many older files the size limit left out.
    left_out: int = 0


class RunInput(Record):
    # The earlier runs whose reports the agent reads, newest first.
    history_run_ids: tuple[int, ...] = ()
    history_files: HistoryFiles | None = None
    payload: Payload | None = None
    # The sender's key, so a repeated delivery finds this run.
    repeat_key: str | None = None
    # An ad hoc task's name, definition, input and requested revision.
    name: str = ""
    definition: dict[str, Any] | None = None
    submission_input: str = ""
    requested_revision: str | None = None

    def stored(self) -> dict[str, Any]:
        """The JSON saved on the run. Queries read repeat_key and name."""
        return self.model_dump(mode="json", exclude_defaults=True)
