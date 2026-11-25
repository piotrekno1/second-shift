"""Each workspace tool call is one timeline line: what it did, never its output."""

from datetime import UTC, datetime
from typing import Any

import pytest
from django.contrib.auth.models import User
from pydantic_ai import RunContext, RunUsage
from pydantic_ai.models.test import TestModel

from secondshift.runner_protocol.sessions import CommandResult
from secondshift.runs.execution.workspace_tools import (
    WorkspaceToolDeps,
    edit_file,
    list_files,
    read_file,
    record_command,
    run_command,
    run_shell,
    search_text,
    write_file,
)
from secondshift.runs.models import Run, RunEvent

LATER = datetime(2100, 1, 1, tzinfo=UTC)


@pytest.fixture
def labels(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    def execute(*args: Any) -> CommandResult:
        sent.append(args[6])
        return CommandResult(call_id=args[2], exit_code=0, output="")

    monkeypatch.setattr(
        "secondshift.runs.execution.workspace_tools.execute_runner_command_step",
        execute,
    )
    return sent


def test_each_tool_names_what_it_did_without_contents_or_arguments(
    labels: list[str],
) -> None:
    deps = WorkspaceToolDeps(
        run_id=1, session_id="run-1", max_output_chars=1_000, deadline=LATER
    )
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage(), tool_call_id="c1")

    list_files(ctx, "src")
    read_file(ctx, "src/app.py")
    read_file(ctx, "src/app.py", 10, 20)
    search_text(ctx, "API_KEY=", "src")
    run_command(ctx, ["/usr/bin/python3", "-c", "print('s3cret')"])
    run_shell(ctx, "curl -H 'Authorization: s3cret' example.com")
    write_file(ctx, "notes.md", "s3cret notes")
    write_file(ctx, "notes.md", "more", append=True)
    edit_file(ctx, "src/app.py", "old", "s3cret")
    read_file(ctx, "a/" * 200 + "deep.py")

    assert labels[:9] == [
        "Listed files in src",
        "Read src/app.py",
        "Read src/app.py, lines 10 to 20",
        "Searched src",
        "Ran python3",
        "Ran a shell command",
        "Wrote notes.md",
        "Appended to notes.md",
        "Edited src/app.py",
    ]
    assert len(labels[9]) == len("Read ") + 160
    assert labels[9].endswith("deep.py")
    assert not any("s3cret" in label or "API_KEY" in label for label in labels)


@pytest.mark.django_db
def test_a_retried_step_records_its_call_once(agent_run: tuple[Run, User]) -> None:
    run, _ = agent_run
    ok = CommandResult(call_id="c", exit_code=0, output="file contents")
    record_command(run.pk, "call-1", "Read README.md", ok)
    record_command(run.pk, "call-1", "Read README.md", ok)
    record_command(
        run.pk,
        "call-2",
        "Ran pytest",
        CommandResult(call_id="c", exit_code=1, output=""),
    )
    record_command(
        run.pk,
        "call-3",
        "Ran a shell command",
        CommandResult(call_id="c", exit_code=124, output="", timed_out=True),
    )

    events = list(RunEvent.objects.filter(run=run).order_by("pk"))
    assert [(event.message, event.level) for event in events] == [
        ("Read README.md", "info"),
        ("Ran pytest · exit 1", "info"),
        ("Ran a shell command · timed out", "warning"),
    ]
    assert "file contents" not in str([event.metadata for event in events])
