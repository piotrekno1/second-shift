"""What the sandbox left behind after a run, as stored in Run.outputs."""

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class SavedFile(Record):
    """A file the agent saved for people to download."""

    name: str
    path: str
    size_bytes: int
    sha256: str = ""


class SkippedFile(Record):
    name: str
    reason: str


class Changes(Record):
    """The agent's changes to the repository. `path` is empty when not kept."""

    file_count: int = 0
    files: tuple[str, ...] = ()
    too_large: bool = False
    path: str = ""
    size_bytes: int | None = None
    sha256: str = ""

    @property
    def kept_file_count(self) -> int:
        return self.file_count if self.path else 0


class RunOutputs(Record):
    changes: Changes = Changes()
    files: tuple[SavedFile, ...] = ()
    skipped: tuple[SkippedFile, ...] = ()

    def saved_file(self, name: str) -> SavedFile | None:
        return next((item for item in self.files if item.name == name), None)

    def stored_paths(self) -> list[str]:
        paths = [item.path for item in self.files] + [self.changes.path]
        return [path for path in paths if path]

    def stored(self) -> dict:
        return self.model_dump(mode="json")
