import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.runner.protocol import CommandResult
from secondshift.runs.execution.run import render_run_input, run_prompt
from secondshift.runs.models import Run
from secondshift.workspaces.services import create_workspace


def capability(
    binding: str,
    *,
    required: bool = False,
    authorized: bool = False,
    missing: str | None = "not connected",
    kind: str = "mcp",
) -> dict[str, object]:
    item: dict[str, object] = {
        "binding": binding,
        "kind": kind,
        "required": required,
        "authorized": authorized,
        "connection_name": f"{binding} production",
    }
    if missing:
        item["missing"] = missing
    return item


@pytest.fixture
def make_run(db):
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project"
    )
    job = Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="triage",
        name="Triage",
        skill_path=".agents/skills/triage/SKILL.md",
        config_hash="abc",
        history=0,
    )

    def make(capabilities: list[dict[str, object]]) -> Run:
        return Run.objects.create(
            job=job,
            scheduled_for=timezone.now(),
            commit_sha="a" * 40,
            input_snapshot={"skill_path": job.skill_path, "job_key": job.key},
            execution_plan={"capabilities": capabilities},
        )

    return make


def test_prompt_lists_optional_bindings_the_run_does_not_have(make_run) -> None:
    run = make_run(
        [
            capability("grafana", authorized=True, missing=None),
            capability("sentry"),
            capability("linear", missing="denied"),
        ]
    )

    prompt = run_prompt(run)

    assert "- `sentry`: not connected" in prompt
    assert "- `linear`: denied" in prompt
    assert "grafana" not in prompt
    # Only the binding name and the reason. No connection details.
    assert "production" not in prompt


def test_prompt_leaves_out_required_and_notification_bindings(make_run) -> None:
    run = make_run(
        [
            capability("github", required=True),
            capability("slack", kind="notification"),
        ]
    )

    assert "optional bindings" not in run_prompt(run)


def test_input_tab_shows_the_missing_bindings(make_run) -> None:
    run = make_run([capability("sentry", missing="waiting for approval")])
    skill = CommandResult(call_id="x", exit_code=0, output="# Triage\n")

    assert "- `sentry`: waiting for approval" in render_run_input(run, skill)
