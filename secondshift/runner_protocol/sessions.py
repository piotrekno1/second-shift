"""The session and command specs the control plane sends to the runner."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from secondshift.runner_protocol.environments import (
    DEFAULT_ENVIRONMENT,
    ENVIRONMENT_NAME_PATTERN,
)
from secondshift.runner_protocol.sandbox import (
    COMMIT_SHA_PATTERN,
    SANDBOX_HISTORY_DIR,
    SANDBOX_OUTPUTS_DIR,
    SANDBOX_SKILL_DIR,
    SANDBOX_WORKSPACE_DIR,
)


def session_id_for(run_id: int, session_key: str) -> str:
    """The runner's id for a run's session.

    The key is random per run, so a reset or restored database that reuses a
    run ID never matches an older session.
    """
    return f"run-{run_id}-{session_key}"


class RunnerProtocolError(RuntimeError):
    pass


class RunnerUnreachable(RunnerProtocolError):
    """The runner could not be reached or did not answer in time."""


REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+$")
COMMIT_SHA = re.compile(COMMIT_SHA_PATTERN)
SESSION_KEY = re.compile(r"^[0-9a-f]{32}$")
ENVIRONMENT_NAME = re.compile(ENVIRONMENT_NAME_PATTERN)
SOURCE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Folders beside the repository where a command may start. Their mounts, and
# whether they are writable, stay fixed by the runner.
COMMAND_FOLDERS = (SANDBOX_SKILL_DIR, SANDBOX_HISTORY_DIR, SANDBOX_OUTPUTS_DIR)
# Setup commands install a job's dependencies before the agent starts.
MAX_SETUP_COMMANDS = 10
MAX_SETUP_COMMAND_CHARS = 1000
# The longest a runner lets one setup command run.
MAX_SETUP_SECONDS = 900
# Setup commands are recorded like agent commands, under IDs only setup uses.
SETUP_CALL_PREFIX = "setup-"


def setup_call_id(index: int) -> str:
    return f"{SETUP_CALL_PREFIX}{index}"


def validate_setup(commands: tuple[str, ...]) -> None:
    if len(commands) > MAX_SETUP_COMMANDS:
        raise ValueError(f"setup takes at most {MAX_SETUP_COMMANDS} commands")
    for command in commands:
        if not command.strip() or "\0" in command:
            raise ValueError("each setup command must be non-empty text")
        if len(command) > MAX_SETUP_COMMAND_CHARS:
            raise ValueError(
                f"each setup command has at most {MAX_SETUP_COMMAND_CHARS} characters"
            )


@dataclass(frozen=True)
class RunnerSessionSpec:
    run_id: int
    session_key: str
    repository: str
    commit_sha: str
    # The environment the job names. The runner maps it to an image, so the
    # control plane never chooses one.
    environment: str
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
    # Shell commands from the job's merged configuration, such as npm ci. The
    # runner runs each one before the agent, with access to package
    # registries only. The network and its allow-list stay server-owned.
    setup: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # JSON brings a list.
        object.__setattr__(self, "setup", tuple(self.setup))
        validate_setup(self.setup)
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
        if self.environment != DEFAULT_ENVIRONMENT and not ENVIRONMENT_NAME.fullmatch(
            self.environment
        ):
            raise ValueError("environment must be an environment name")
        if not SOURCE_SHA256.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 must be a lowercase SHA-256")
        if self.history_sha256 and not SOURCE_SHA256.fullmatch(self.history_sha256):
            raise ValueError("history_sha256 must be empty or a lowercase SHA-256")
        if self.skill_sha256 and not SOURCE_SHA256.fullmatch(self.skill_sha256):
            raise ValueError("skill_sha256 must be empty or a lowercase SHA-256")

    @property
    def session_id(self) -> str:
        return session_id_for(self.run_id, self.session_key)


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
        if ".." in path.parts or (
            path.is_absolute()
            and not any(path.is_relative_to(folder) for folder in COMMAND_FOLDERS)
        ):
            raise ValueError("cwd must stay inside the workspace or a sandbox folder")
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("timeout_seconds must be between 1 and 900")

    @property
    def workdir(self) -> str:
        """The command's folder in the sandbox."""
        path = PurePosixPath(self.cwd)
        if path.is_absolute():
            return path.as_posix()
        return (PurePosixPath(SANDBOX_WORKSPACE_DIR) / path).as_posix()

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
