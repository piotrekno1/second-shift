import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath


class RunnerProtocolError(RuntimeError):
    pass


REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40,64}$")
SESSION_KEY = re.compile(r"^[0-9a-f]{32}$")
SOURCE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RunnerSessionSpec:
    run_id: int
    session_key: str
    repository: str
    commit_sha: str
    image: str
    # The uploaded snapshot of the commit's files the session unpacks.
    source_sha256: str
    # Record the unpacked files, so the agent's changes can be collected.
    # The control plane sets it from the run's plan, never from repository input.
    collect_changes: bool = False
    # An uploaded archive of earlier runs' saved files, mounted read-only.
    # Empty when the run has none.
    history_sha256: str = ""
    # An uploaded archive of a skill from another repository, mounted
    # read-only. Empty when the skill is in the run's repository or there is
    # none. The control plane builds it, so the skill's repository never
    # chooses where it goes or how it is mounted.
    skill_sha256: str = ""

    def __post_init__(self) -> None:
        if self.run_id < 1:
            raise ValueError("run_id must be positive")
        if not SESSION_KEY.fullmatch(self.session_key):
            raise ValueError("session_key must be 32 lowercase hexadecimal characters")
        if not REPOSITORY_NAME.fullmatch(self.repository):
            raise ValueError("repository must be a normalized owner/name path")
        if any(part in {".", ".."} for part in self.repository.split("/")):
            raise ValueError("repository must be a normalized owner/name path")
        if not COMMIT_SHA.fullmatch(self.commit_sha):
            raise ValueError("commit_sha must be a full lowercase hexadecimal SHA")
        if not self.image.strip():
            raise ValueError("a server-owned image is required")
        if not SOURCE_SHA256.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 must be a lowercase SHA-256")
        if self.history_sha256 and not SOURCE_SHA256.fullmatch(self.history_sha256):
            raise ValueError("history_sha256 must be empty or a lowercase SHA-256")
        if self.skill_sha256 and not SOURCE_SHA256.fullmatch(self.skill_sha256):
            raise ValueError("skill_sha256 must be empty or a lowercase SHA-256")

    @property
    def session_id(self) -> str:
        # The key is random per run, so a reset or restored database that
        # reuses a run ID never matches an older session.
        return f"run-{self.run_id}-{self.session_key}"


@dataclass(frozen=True)
class CommandRequest:
    call_id: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if not self.call_id.strip():
            raise ValueError("call_id is required")
        if not self.argv or any(
            not argument or "\0" in argument for argument in self.argv
        ):
            raise ValueError("argv must contain non-empty arguments")
        path = PurePosixPath(self.cwd)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("cwd must stay inside the workspace")
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("timeout_seconds must be between 1 and 900")

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class CommandResult:
    call_id: str
    exit_code: int | None
    output: str
    timed_out: bool = False
