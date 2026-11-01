import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Any

from dbos import DBOS
from pydantic import Field
from pydantic_ai import RunContext, Tool
from pydantic_ai.tools import ToolDefinition

from secondshift.jobs.tool_policy import LOCAL_TOOLS
from secondshift.runner.client import get_runner_client
from secondshift.runner.protocol import CommandRequest, CommandResult
from secondshift.runs.execution.deadlines import (
    check_deadline,
)

MODEL_TOOL_NAMES = {
    "workspace.list_files": "workspace_list_files",
    "workspace.read_file": "workspace_read_file",
    "workspace.search_text": "workspace_search_text",
    "workspace.run_command": "workspace_run_command",
}


PathArgument = Annotated[str, Field(min_length=1, max_length=1_000)]


SearchArgument = Annotated[str, Field(min_length=1, max_length=1_000)]


CommandArgument = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=16_384)]],
    Field(min_length=1, max_length=256),
]


TimeoutArgument = Annotated[int, Field(ge=1, le=900)]


@dataclass(frozen=True)
class WorkspaceToolDeps:
    run_id: int
    session_id: str
    max_output_chars: int
    deadline: datetime
    allowed_tools: tuple[str, ...] = LOCAL_TOOLS
    # What the input snapshot records about a trigger's request body, if any.
    payload: dict[str, Any] | None = None

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
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Workspace paths must stay inside the repository.")
    return path.as_posix()


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
    """List repository files recursively beneath a relative path."""
    return ctx.deps.execute(
        ctx.tool_call_id,
        ["find", workspace_path(path), "-type", "f", "-print"],
    )


def read_file(ctx: RunContext[WorkspaceToolDeps], path: PathArgument) -> dict[str, Any]:
    """Read a text file at a relative repository path."""
    return ctx.deps.execute(
        ctx.tool_call_id,
        ["cat", "--", workspace_path(path)],
    )


def search_text(
    ctx: RunContext[WorkspaceToolDeps],
    query: SearchArgument,
    path: PathArgument = ".",
) -> dict[str, Any]:
    """Search repository text recursively using a fixed-string query."""
    if "\0" in query:
        raise ValueError("Search text cannot contain NUL bytes.")
    return ctx.deps.execute(
        ctx.tool_call_id,
        ["grep", "-R", "-n", "-F", "--", query, workspace_path(path)],
    )


def run_command(
    ctx: RunContext[WorkspaceToolDeps],
    argv: CommandArgument,
    cwd: PathArgument = ".",
    timeout_seconds: TimeoutArgument = 120,
) -> dict[str, Any]:
    """Run an argv command in the offline sandbox without invoking a shell."""
    return ctx.deps.execute(
        ctx.tool_call_id,
        argv,
        cwd=workspace_path(cwd),
        timeout_seconds=timeout_seconds,
    )


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
    }
    return tuple(
        Tool(
            functions[name],
            name=MODEL_TOOL_NAMES[name],
            prepare=prepare_tool(name),
        )
        for name in LOCAL_TOOLS
    )
