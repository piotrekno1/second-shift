from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import storages
from django.utils import timezone
from pydantic_ai import ModelRetry, RunContext, RunUsage
from pydantic_ai.models.test import TestModel

from secondshift.config.constants import RunStart
from secondshift.jobs.models import Job
from secondshift.jobs.tests.plans import CAPABILITY, stored_plan
from secondshift.jobs.tool_policy import LOCAL_TOOLS
from secondshift.repositories.models import Repository
from secondshift.runner.protocol import (
    CommandRequest,
    CommandResult,
)
from secondshift.runs.dao import create_task
from secondshift.runs.execution.run import WORKSPACE_AGENT, execute_agent_run
from secondshift.runs.execution.workspace_tools import (
    WorkspaceToolDeps,
    read_file,
    run_command,
    workspace_tools,
)
from secondshift.runs.models import Run
from secondshift.workspaces.dao import create_workspace

LATER = datetime(2100, 1, 1, tzinfo=UTC)


def tool_context(
    deps: WorkspaceToolDeps, call_id: str = "model-call-1"
) -> RunContext[WorkspaceToolDeps]:

    return RunContext(
        deps=deps,
        model=TestModel(),
        usage=RunUsage(),
        tool_call_id=call_id,
    )


def test_workspace_tools_come_only_from_the_frozen_plan() -> None:
    deps = WorkspaceToolDeps(
        run_id=1,
        session_id="run-1",
        max_output_chars=100,
        deadline=LATER,
        allowed_tools=("workspace.read_file", "workspace.run_command"),
    )
    ctx = tool_context(deps)
    tools = workspace_tools()
    prepared = [anyio.run(tool.prepare_tool_def, ctx) for tool in tools]

    assert [definition.name for definition in prepared if definition] == [
        "workspace_read_file",
        "workspace_run_command",
    ]


def test_file_tools_reject_paths_outside_the_workspace() -> None:
    deps = WorkspaceToolDeps(
        run_id=1, session_id="run-1", max_output_chars=100, deadline=LATER
    )
    ctx = tool_context(deps)

    with pytest.raises(ModelRetry, match="inside the repository"):
        read_file(ctx, "/etc/passwd")
    with pytest.raises(ModelRetry, match="inside the repository"):
        read_file(ctx, "../../secret")
    with pytest.raises(ModelRetry, match="inside the repository"):
        run_command(ctx, ["ls"], cwd="/")


def test_runner_call_is_stable_bounded_and_contains_no_runtime_configuration(
    monkeypatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def execute(*args: Any) -> CommandResult:
        calls.append(args)
        return CommandResult(
            call_id=str(args[1]),
            exit_code=0,
            output="a" * 500,
        )

    monkeypatch.setattr(
        "secondshift.runs.execution.workspace_tools.execute_runner_command_step",
        execute,
    )
    deps = WorkspaceToolDeps(
        run_id=7, session_id="run-7", max_output_chars=80, deadline=LATER
    )
    ctx = tool_context(deps)

    result = run_command(ctx, ["python", "-m", "pytest"], "src", 30)
    run_command(ctx, ["python", "-m", "pytest"], "src", 30)

    assert result["output_truncated"] is True
    assert len(result["output"]) == 80
    assert calls[0] == calls[1]
    assert calls[0][0] == "run-7"
    assert calls[0][2:] == (["python", "-m", "pytest"], "src", 30)
    assert len(calls[0][1]) == 64
    assert not any(
        value in str(calls) for value in ("--mount", "--network", "--runtime", "secret")
    )


def test_pydantic_agent_calls_the_runner_backed_tool(monkeypatch) -> None:
    requests: list[CommandRequest] = []

    def execute(
        session_id: str,
        call_id: str,
        argv: list[str],
        cwd: str,
        timeout_seconds: int,
    ) -> CommandResult:
        requests.append(CommandRequest(call_id, tuple(argv), cwd, timeout_seconds))
        return CommandResult(call_id=call_id, exit_code=0, output="skill text")

    monkeypatch.setattr(
        "secondshift.runs.execution.workspace_tools.execute_runner_command_step",
        execute,
    )
    model = TestModel(
        call_tools=["workspace_read_file"],
        custom_output_text="Review complete.",
    )

    with WORKSPACE_AGENT.override(model=model):
        result = WORKSPACE_AGENT.run_sync(
            "Read the skill.",
            deps=WorkspaceToolDeps(
                run_id=9,
                session_id="session-9",
                max_output_chars=1_000,
                deadline=LATER,
            ),
            run_id="test-runner-tool",
        )

    assert result.output == "Review complete."
    assert requests[0].argv == ("cat", "--", "a")


@pytest.mark.django_db
def test_execute_agent_run_stores_report_and_uses_native_limits(
    settings,
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings.STORAGES = {
        **settings.STORAGES,
        "run_artifacts": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": tmp_path / "artifacts", "base_url": None},
        },
    }
    storages._storages.clear()
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace,
        full_name="example/project",
        config_sha="a" * 40,
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        config_hash="hash",
    )
    run = Run.objects.create(
        task=create_task(repository=job.repository),
        started_by=RunStart.SCHEDULE,
        job=job,
        commit_sha="a" * 40,
        scheduled_for=timezone.now(),
        execution_plan=stored_plan(
            job={"key": job.key, "skill_path": job.skill_path},
            agent={
                "tools": list(LOCAL_TOOLS),
                "limits": {
                    "max_turns": 8,
                    "max_tool_calls": 20,
                    "max_tool_result_chars": 1_000,
                },
            },
        ),
    )
    captured: dict[str, Any] = {}

    class FakeAgent:
        def run_sync(self, prompt: str, **kwargs: Any):
            captured["prompt"] = prompt
            captured.update(kwargs)
            return SimpleNamespace(
                output="Repository health is good.", usage=RunUsage(requests=2)
            )

    monkeypatch.setattr("secondshift.runs.execution.run.WORKSPACE_AGENT", FakeAgent())

    result = execute_agent_run(run, "run-1")

    assert result["succeeded"] is True
    assert result["summary"] == "Repository health is good."
    assert captured["usage_limits"].request_limit == 8
    assert captured["usage_limits"].tool_calls_limit == 20
    assert job.skill_path in captured["prompt"]
    assert captured["deps"].allowed_tools == LOCAL_TOOLS
    with storages["run_artifacts"].open(result["output"]["path"]) as stored:
        assert stored.read().decode() == "Repository health is good."

    run.execution_plan["capabilities"] = [
        CAPABILITY | {"authorized": True, "effective_tools": ["query_metrics"]}
    ]
    run.save(update_fields=["execution_plan"])
    with pytest.raises(ValueError, match="no longer approved"):
        execute_agent_run(Run.objects.get(pk=run.pk), "run-1")
