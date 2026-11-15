import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import ModelRetry, RunContext, RunUsage
from pydantic_ai.models.test import TestModel

from secondshift.runner.protocol import CommandResult
from secondshift.runs.execution.workspace_tools import (
    MAX_WRITE_BYTES,
    WorkspaceToolDeps,
    edit_file,
    read_file,
    run_command,
    run_shell,
    write_file,
)

LATER = datetime(2100, 1, 1, tzinfo=UTC)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], str]]:
    """Each command the tools send to the runner, with its folder."""
    sent: list[tuple[list[str], str]] = []

    def execute(
        session_id: str, call_id: str, argv: list[str], cwd: str, timeout: int
    ) -> CommandResult:
        sent.append((argv, cwd))
        return CommandResult(call_id=call_id, exit_code=0, output="")

    monkeypatch.setattr(
        "secondshift.runs.execution.workspace_tools.execute_runner_command_step",
        execute,
    )
    return sent


@pytest.fixture
def ctx() -> RunContext[WorkspaceToolDeps]:
    deps = WorkspaceToolDeps(
        run_id=1, session_id="run-1", max_output_chars=1_000, deadline=LATER
    )
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage(), tool_call_id="c1")


def run_in(folder: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a tool's command here, as the sandbox would in /workspace."""
    return subprocess.run(argv, cwd=folder, capture_output=True, text=True, check=False)


def test_shell_runs_bash_in_the_chosen_folder(ctx, calls) -> None:
    run_shell(ctx, "pytest -q | tail -1 && echo done", cwd="/skill/scripts")

    assert calls == [
        (["bash", "-c", "pytest -q | tail -1 && echo done"], "/skill/scripts")
    ]


@pytest.mark.parametrize(
    ("cwd", "sent"),
    [
        ("/skill", "/skill"),
        ("/history/run-3", "/history/run-3"),
        ("/outputs", "/outputs"),
        ("/workspace/src", "src"),
        ("/workspace", "."),
    ],
)
def test_commands_may_start_beside_the_repository(ctx, calls, cwd, sent) -> None:
    run_command(ctx, ["ls"], cwd=cwd)

    assert calls[0][1] == sent


@pytest.mark.parametrize(
    "cwd", ["/", "/etc", "/auth", "/skill/../etc", "/skillset", "../x"]
)
def test_commands_cannot_start_elsewhere(ctx, calls, cwd) -> None:
    with pytest.raises(ModelRetry, match="inside the repository"):
        run_shell(ctx, "ls", cwd=cwd)
    assert calls == []


@pytest.mark.parametrize(
    "path", ["/skill/SKILL.md", "/history/a.csv", "/etc/x", "../x"]
)
def test_files_are_written_only_in_the_repository_or_outputs(ctx, calls, path) -> None:
    with pytest.raises(ModelRetry):
        write_file(ctx, path, "text")
    with pytest.raises(ModelRetry):
        edit_file(ctx, path, "a", "b")
    assert calls == []


def test_writes_reject_content_too_long_for_one_argument(ctx, calls) -> None:
    # Two bytes per character, so the character count alone would pass.
    with pytest.raises(ModelRetry, match="append"):
        write_file(ctx, "notes.md", "é" * (MAX_WRITE_BYTES // 2 + 1))
    assert calls == []


def test_read_file_can_read_a_range_of_lines(ctx, calls) -> None:
    read_file(ctx, "README.md", start_line=10, end_line=20)
    read_file(ctx, "README.md", start_line=30)

    assert [argv for argv, _ in calls] == [
        ["sed", "-n", "10,20p", "--", "README.md"],
        ["sed", "-n", "30,$p", "--", "README.md"],
    ]


TRICKY = "line one\n  $HOME `whoami` \"quoted\" 'single' %s %d \\n\nzażółć\n"


@pytest.mark.parametrize("content", [TRICKY, "", "no newline", "-n starts with a dash"])
def test_written_content_arrives_unchanged(ctx, calls, tmp_path, content) -> None:
    write_file(ctx, "docs/new/notes.md", content)
    result = run_in(tmp_path, calls[0][0])

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docs/new/notes.md").read_text() == content


def test_append_adds_to_the_end(ctx, calls, tmp_path) -> None:
    write_file(ctx, "notes.md", "first\n")
    write_file(ctx, "notes.md", "second\n", append=True)
    for argv, _ in calls:
        run_in(tmp_path, argv)

    assert (tmp_path / "notes.md").read_text() == "first\nsecond\n"


def edit(ctx, calls, folder: Path, *args: Any, **kwargs: Any):
    calls.clear()
    edit_file(ctx, "app.py", *args, **kwargs)
    return run_in(folder, calls[0][0])


def test_edit_replaces_one_exact_match(ctx, calls, tmp_path) -> None:
    (tmp_path / "app.py").write_text("a = 1\nb = 'x'\r\nc = 1\n")

    result = edit(ctx, calls, tmp_path, "b = 'x'", 'b = "$y" %s')

    assert result.returncode == 0, result.stderr
    # Other lines, including Windows line endings, stay as they were.
    assert (tmp_path / "app.py").read_bytes() == b'a = 1\nb = "$y" %s\r\nc = 1\n'


def test_edit_refuses_missing_or_repeated_text(ctx, calls, tmp_path) -> None:
    (tmp_path / "app.py").write_text("x = 1\nx = 1\n")

    repeated = edit(ctx, calls, tmp_path, "x = 1", "x = 2")
    missing = edit(ctx, calls, tmp_path, "y = 1", "y = 2")

    assert repeated.returncode == 1 and "appears 2 times" in repeated.stderr
    assert missing.returncode == 1 and "Read the file again" in missing.stderr
    assert (tmp_path / "app.py").read_text() == "x = 1\nx = 1\n"


def test_edit_can_replace_every_match_or_delete(ctx, calls, tmp_path) -> None:
    (tmp_path / "app.py").write_text("x = 1\nx = 1\n# remove me\n")

    edit(ctx, calls, tmp_path, "x = 1", "x = 2", replace_all=True)
    edit(ctx, calls, tmp_path, "# remove me\n", "")

    assert (tmp_path / "app.py").read_text() == "x = 2\nx = 2\n"


def test_edit_points_to_write_for_a_new_file(ctx, calls, tmp_path) -> None:
    result = edit(ctx, calls, tmp_path, "a", "b")

    assert "workspace_write_file" in result.stderr
