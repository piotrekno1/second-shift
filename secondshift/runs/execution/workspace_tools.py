import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any

from dbos import DBOS
from pydantic import Field
from pydantic_ai import ModelRetry, RunContext, Tool
from pydantic_ai.tools import ToolDefinition

from secondshift.config.constants import (
    SANDBOX_HISTORY_DIR,
    SANDBOX_OUTPUTS_DIR,
    SANDBOX_SKILL_DIR,
    SANDBOX_WORKSPACE_DIR,
)
from secondshift.jobs.tool_policy import LOCAL_TOOLS
from secondshift.runner.client import get_runner_client
from secondshift.runner.protocol import CommandRequest, CommandResult
from secondshift.runs.execution.deadlines import (
    check_deadline,
)
from secondshift.runs.inputs import Payload

MODEL_TOOL_NAMES = {
    "workspace.list_files": "workspace_list_files",
    "workspace.read_file": "workspace_read_file",
    "workspace.search_text": "workspace_search_text",
    "workspace.run_command": "workspace_run_command",
    "workspace.run_shell": "workspace_run_shell",
    "workspace.write_file": "workspace_write_file",
    "workspace.edit_file": "workspace_edit_file",
}


PathArgument = Annotated[str, Field(min_length=1, max_length=1_000)]


SearchArgument = Annotated[str, Field(min_length=1, max_length=1_000)]


CommandArgument = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=16_384)]],
    Field(min_length=1, max_length=256),
]


TimeoutArgument = Annotated[int, Field(ge=1, le=900)]


ShellArgument = Annotated[str, Field(min_length=1, max_length=65_536)]


# Linux refuses a single argument over 128 KiB, and file content travels as one.
MAX_WRITE_BYTES = 100_000


ContentArgument = Annotated[str, Field(max_length=MAX_WRITE_BYTES)]


EditArgument = Annotated[str, Field(max_length=MAX_WRITE_BYTES // 2)]


LineArgument = Annotated[int, Field(ge=1)]


@dataclass(frozen=True)
class WorkspaceToolDeps:
    run_id: int
    session_id: str
    max_output_chars: int
    deadline: datetime
    allowed_tools: tuple[str, ...] = LOCAL_TOOLS
    # What the input snapshot records about a trigger's request body, if any.
    payload: Payload | None = None
    # How many issues the agent may propose. Zero hides the tool.
    issue_limit: int = 0
    # How many sub-agents the run may start. Zero hides the tool.
    subagent_limit: int = 0

    def execute(
        self,
        tool_call_id: str | None,
        argv: list[str],
        *,
        cwd: str = ".",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        if not tool_call_id:
            raise RuntimeError("The model tool call has no stable identifier.")
        check_deadline(self.deadline)
        call_id = hashlib.sha256(f"{self.run_id}:{tool_call_id}".encode()).hexdigest()
        result = execute_runner_command_step(
            self.session_id,
            call_id,
            argv,
            cwd,
            timeout_seconds,
        )
        output, truncated = truncate_output(result.output, self.max_output_chars)
        return {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "output": output,
            "output_truncated": truncated,
        }


@DBOS.step(
    name="secondshift.execute_runner_command",
    retries_allowed=True,
    max_attempts=3,
)
def execute_runner_command_step(
    session_id: str,
    call_id: str,
    argv: list[str],
    cwd: str,
    timeout_seconds: int,
) -> CommandResult:
    """Cross the authenticated runner boundary in one durable, idempotent step."""
    return get_runner_client().execute(
        session_id,
        CommandRequest(
            call_id=call_id,
            argv=tuple(argv),
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        ),
    )


def workspace_path(value: str) -> str:
    if "\0" in value:
        raise ValueError("Workspace paths cannot contain NUL bytes.")
    path = PurePosixPath(value)
    # Models often name repository files by their absolute sandbox path.
    if path.is_relative_to(SANDBOX_WORKSPACE_DIR):
        path = path.relative_to(SANDBOX_WORKSPACE_DIR)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Workspace paths must stay inside the repository.")
    return path.as_posix()


def argument_path(value: str) -> str:
    """Check a path the model sent. A bad one goes back to the model to fix."""
    try:
        return workspace_path(value)
    except ValueError as error:
        raise ModelRetry(str(error)) from None


# Read-only folders beside the repository: earlier runs' saved files, and a
# skill from another repository.
READABLE_FOLDERS = (SANDBOX_HISTORY_DIR, SANDBOX_SKILL_DIR)


def sandbox_path(value: str) -> str:
    """A repository path, or one under a read-only folder. Raises ValueError."""
    path = PurePosixPath(value)
    if (
        any(path.is_relative_to(folder) for folder in READABLE_FOLDERS)
        and ".." not in path.parts
        and "\0" not in value
    ):
        return path.as_posix()
    if path.is_absolute():
        raise ValueError(
            "Paths must stay inside the repository, "
            f"{SANDBOX_HISTORY_DIR} or {SANDBOX_SKILL_DIR}."
        )
    return workspace_path(value)


def folder_path(value: str, folders: tuple[str, ...]) -> str:
    """A repository path, or one under one of the given sandbox folders.

    Raises ModelRetry, so a bad path goes back to the model to fix.
    """
    path = PurePosixPath(value)
    if (
        any(path.is_relative_to(folder) for folder in folders)
        and ".." not in path.parts
        and "\0" not in value
    ):
        return path.as_posix()
    try:
        return workspace_path(value)
    except ValueError:
        raise ModelRetry(
            f"Paths must stay inside the repository or {', '.join(folders)}."
        ) from None


def writable_path(value: str) -> str:
    """A repository path, or one under /outputs."""
    path = folder_path(value, (SANDBOX_OUTPUTS_DIR,))
    if path in {".", SANDBOX_OUTPUTS_DIR}:
        raise ModelRetry("Name a file, not a folder.")
    return path


def command_folder(value: str) -> str:
    """Where a command may start: the repository or a folder beside it."""
    return folder_path(value, (*READABLE_FOLDERS, SANDBOX_OUTPUTS_DIR))


def readable_path(value: str) -> str:
    """A path the model sent to read. A bad one goes back to the model to fix."""
    try:
        return sandbox_path(value)
    except ValueError as error:
        raise ModelRetry(str(error)) from None


def truncate_output(output: str, limit: int) -> tuple[str, bool]:
    if len(output) <= limit:
        return output, False
    marker = "\n[... tool output truncated by SecondShift ...]\n"
    remaining = max(limit - len(marker), 0)
    head = remaining // 2
    tail = remaining - head
    bounded = output[:head] + marker + (output[-tail:] if tail else "")
    return bounded[:limit], True


def list_files(
    ctx: RunContext[WorkspaceToolDeps], path: PathArgument = "."
) -> dict[str, Any]:
    """List files recursively beneath a relative repository path, or under
    /history, where earlier runs' saved files are, or /skill."""
    return ctx.deps.execute(
        ctx.tool_call_id,
        ["find", readable_path(path), "-type", "f", "-print"],
    )


def read_file(
    ctx: RunContext[WorkspaceToolDeps],
    path: PathArgument,
    start_line: LineArgument | None = None,
    end_line: LineArgument | None = None,
) -> dict[str, Any]:
    """Read a text file at a relative repository path, or under /history or
    /skill. Give start_line and end_line to read only those lines of a long
    file."""
    if start_line is None and end_line is None:
        return ctx.deps.execute(ctx.tool_call_id, ["cat", "--", readable_path(path)])
    first = start_line or 1
    last = end_line or "$"
    if end_line is not None and end_line < first:
        raise ModelRetry("end_line must not be before start_line.")
    return ctx.deps.execute(
        ctx.tool_call_id, ["sed", "-n", f"{first},{last}p", "--", readable_path(path)]
    )


def search_text(
    ctx: RunContext[WorkspaceToolDeps],
    query: SearchArgument,
    path: PathArgument = ".",
) -> dict[str, Any]:
    """Search text recursively using a fixed-string query, beneath a relative
    repository path or under /history or /skill."""
    if "\0" in query:
        raise ModelRetry("Search text cannot contain NUL bytes.")
    return ctx.deps.execute(
        ctx.tool_call_id,
        ["grep", "-R", "-n", "-F", "--", query, readable_path(path)],
    )


def run_command(
    ctx: RunContext[WorkspaceToolDeps],
    argv: CommandArgument,
    cwd: PathArgument = ".",
    timeout_seconds: TimeoutArgument = 120,
) -> dict[str, Any]:
    """Run an argv command in the offline sandbox without invoking a shell.

    cwd is a repository path, or a folder under /skill, /history or /outputs.
    """
    return ctx.deps.execute(
        ctx.tool_call_id,
        argv,
        cwd=command_folder(cwd),
        timeout_seconds=timeout_seconds,
    )


def run_shell(
    ctx: RunContext[WorkspaceToolDeps],
    command: ShellArgument,
    cwd: PathArgument = ".",
    timeout_seconds: TimeoutArgument = 120,
) -> dict[str, Any]:
    """Run a bash command line in the offline sandbox, with pipes, redirects,
    && and globs. Each call starts a new shell in cwd: a repository path, or a
    folder under /skill, /history or /outputs."""
    return ctx.deps.execute(
        ctx.tool_call_id,
        ["bash", "-c", command],
        cwd=command_folder(cwd),
        timeout_seconds=timeout_seconds,
    )


# The content is one argument, so it never passes through a shell's parser.
WRITE_SCRIPT = 'mkdir -p -- "$(dirname -- "$1")" && printf %s "$2" {} "$1"'
EMPTY_SCRIPT = 'mkdir -p -- "$(dirname -- "$1")" && : {} "$1"'


def write_file(
    ctx: RunContext[WorkspaceToolDeps],
    path: PathArgument,
    content: ContentArgument,
    append: bool = False,
) -> dict[str, Any]:
    """Create or overwrite a text file in the repository or under /outputs,
    with its parent folders. Set append to add to the end of the file instead.
    A file over 100,000 bytes takes several calls: write the first part, then
    append the rest."""
    target = writable_path(path)
    if "\0" in content:
        raise ModelRetry("File content cannot contain NUL bytes.")
    if len(content.encode()) > MAX_WRITE_BYTES:
        raise ModelRetry(
            f"Content is over {MAX_WRITE_BYTES:,} bytes. Write the first part, "
            "then add the rest with append set."
        )
    redirect = ">>" if append else ">"
    if content:
        argv = ["sh", "-c", WRITE_SCRIPT.format(redirect), "sh", target, content]
    else:
        argv = ["sh", "-c", EMPTY_SCRIPT.format(redirect), "sh", target]
    result = ctx.deps.execute(ctx.tool_call_id, argv)
    if result["exit_code"] == 0 and not result["output"]:
        result["output"] = f"Wrote {len(content.encode()):,} bytes to {target}."
    return result


# Runs in the sandbox. The texts are JSON, because an argument cannot be empty.
EDIT_SCRIPT = """import json, sys
path, old, new, every = sys.argv[1], *map(json.loads, sys.argv[2:4]), sys.argv[4] == "all"
try:
    with open(path, encoding="utf-8", newline="") as stream:
        text = stream.read()
except FileNotFoundError:
    sys.exit(f"There is no file at {path}. Create it with workspace_write_file.")
except UnicodeDecodeError:
    sys.exit(f"{path} is not UTF-8 text.")
count = text.count(old)
if not count:
    sys.exit(f"old_text is not in {path}. Read the file again and copy the text exactly.")
if count > 1 and not every:
    sys.exit(f"old_text appears {count} times in {path}. Include more of the "
             "surrounding text so it appears once, or set replace_all.")
with open(path, "w", encoding="utf-8", newline="") as stream:
    stream.write(text.replace(old, new) if every else text.replace(old, new, 1))
print(f"Replaced {count if every else 1} occurrence(s) in {path}.")
"""


def edit_file(
    ctx: RunContext[WorkspaceToolDeps],
    path: PathArgument,
    old_text: EditArgument,
    new_text: EditArgument,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Replace exact text in a file in the repository or under /outputs.
    old_text must appear exactly once, unless replace_all is set. Copy it from
    the file with its whitespace. An empty new_text deletes it."""
    if not old_text:
        raise ModelRetry("old_text cannot be empty. Use workspace_write_file instead.")
    if "\0" in old_text or "\0" in new_text:
        raise ModelRetry("Text cannot contain NUL bytes.")
    argv = [
        "python3",
        "-c",
        EDIT_SCRIPT,
        writable_path(path),
        json.dumps(old_text, ensure_ascii=False),
        json.dumps(new_text, ensure_ascii=False),
        "all" if replace_all else "one",
    ]
    return ctx.deps.execute(ctx.tool_call_id, argv)


def prepare_tool(
    canonical_name: str,
) -> Callable[
    [RunContext[WorkspaceToolDeps], ToolDefinition],
    Awaitable[ToolDefinition | None],
]:
    async def prepare(
        ctx: RunContext[WorkspaceToolDeps], definition: ToolDefinition
    ) -> ToolDefinition | None:
        return definition if canonical_name in ctx.deps.allowed_tools else None

    return prepare


def workspace_tools() -> tuple[Tool[WorkspaceToolDeps], ...]:
    """Build the fixed local toolset registered with the durable agent."""
    functions = {
        "workspace.list_files": list_files,
        "workspace.read_file": read_file,
        "workspace.search_text": search_text,
        "workspace.run_command": run_command,
        "workspace.run_shell": run_shell,
        "workspace.write_file": write_file,
        "workspace.edit_file": edit_file,
    }
    return tuple(
        Tool(
            functions[name],
            name=MODEL_TOOL_NAMES[name],
            prepare=prepare_tool(name),
        )
        for name in LOCAL_TOOLS
    )
