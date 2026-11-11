"""Tasks whose skill comes from another public repository."""

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User

from secondshift.config.constants import RunStart
from secondshift.mcp_server.tests.test_job_tools import call, error_text, token
from secondshift.repositories.models import Repository
from secondshift.repositories.tests.test_external_skills import (
    PATH,
    REPOSITORY,
)
from secondshift.repositories.tests.test_external_skills import (
    vendor as vendor_fixture,
)
from secondshift.runner.protocol import CommandResult
from secondshift.runs.execution import run as execution
from secondshift.runs.execution.run import capture_run_input_step, run_prompt
from secondshift.runs.models import Run, Task
from secondshift.runs.orchestration.workflows import (
    prepare_source_step,
    start_runner_session_step,
)
from secondshift.runs.serialization import serialize_run_detail
from secondshift.runs.services import RunRefused
from secondshift.runs.submissions import SkillSubmission, submit_skill
from secondshift.runs.tests.test_submissions import (
    owner as owner_fixture,
)
from secondshift.runs.tests.test_submissions import (
    queued as queued_fixture,
)
from secondshift.runs.tests.test_submissions import (
    repository as repository_fixture,
)

pytestmark = pytest.mark.django_db(transaction=True)
owner = owner_fixture
queued = queued_fixture
repository = repository_fixture
vendor = vendor_fixture


def start(repository: Repository, owner: User, ref: str | None = "main") -> Run:
    source: dict[str, Any] = {"repository": REPOSITORY}
    if ref is not None:
        source["ref"] = ref
    submission = SkillSubmission.model_validate(
        {
            "repository_id": repository.pk,
            "definition": {"name": "Triage", "skill": PATH, "skill_source": source},
            "idempotency_key": f"triage-{ref}",
        }
    )
    return submit_skill(repository, submission, owner, RunStart.MANUAL).run


def head(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def test_a_branch_is_pinned_and_the_repository_keeps_its_own_commit(
    repository: Repository, owner: User, vendor: Path
) -> None:
    run = start(repository, owner)

    job = run.execution_plan["job"]
    assert job["skill_path"] == PATH
    assert job["skill_source"] == {"repository": REPOSITORY, "commit_sha": head(vendor)}
    assert run.commit_sha == head(Path(repository.local_path))
    prompt = run_prompt(run)
    assert "Read /skill/SKILL.md with workspace_read_file" in prompt
    assert f"comes from {REPOSITORY} at {head(vendor)[:12]}" in prompt
    detail = serialize_run_detail(run)
    assert detail.skill_source is not None
    assert detail.skill_source.url.endswith(f"/{head(vendor)}/{PATH}")


def test_an_unavailable_skill_creates_nothing(
    repository: Repository, owner: User, vendor: Path
) -> None:
    with pytest.raises(RunRefused, match="Could not fetch missing"):
        start(repository, owner, ref="missing")
    assert not Task.objects.exists()


def test_the_runner_gets_only_the_skill_folder_beside_the_repository(
    repository: Repository,
    owner: User,
    vendor: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = start(repository, owner)
    prepare_source_step.__wrapped__(run.pk)
    runner = Mock()
    runner.start.return_value = "sandbox-1"
    monkeypatch.setattr(
        "secondshift.runs.orchestration.workflows.get_runner_client", lambda: runner
    )

    start_runner_session_step.__wrapped__(run.pk)

    spec = runner.start.call_args.args[0]
    assert spec.commit_sha == run.commit_sha
    assert spec.skill_sha256
    assert spec.skill_sha256 != spec.source_sha256
    uploaded = {call.args[0] for call in runner.upload_source.call_args_list}
    assert uploaded == {spec.source_sha256, spec.skill_sha256}
    assert run.events.filter(message__contains=f"from {REPOSITORY}").exists()


def test_the_stored_input_shows_the_skill_from_its_folder(
    repository: Repository,
    owner: User,
    vendor: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = start(repository, owner)
    runner = Mock()
    runner.execute.return_value = CommandResult(
        call_id="c", exit_code=0, output="# Triage"
    )
    monkeypatch.setattr(execution, "get_runner_client", lambda: runner)

    capture_run_input_step(run.pk, "sandbox-1")

    assert runner.execute.call_args.args[1].argv == ("cat", "--", "/skill/SKILL.md")


def test_a_repository_cannot_choose_the_mount(
    repository: Repository, owner: User, vendor: Path
) -> None:
    """The definition names a skill. Paths outside it never reach the runner."""
    for path in ["../../etc/SKILL.md", "/skill/SKILL.md"]:
        with pytest.raises(RunRefused, match="inside its repository"):
            submission = SkillSubmission.model_validate(
                {
                    "repository_id": repository.pk,
                    "definition": {
                        "name": "Triage",
                        "skill": "SKILL.md",
                        "skill_source": {"repository": REPOSITORY, "ref": "main"},
                    },
                    "idempotency_key": "triage",
                }
            )
            definition = submission.definition.model_copy(update={"skill": path})
            submit_skill(
                repository,
                submission.model_copy(update={"definition": definition}),
                owner,
                RunStart.MANUAL,
            )


def test_without_a_ref_the_default_branch_is_used(
    repository: Repository, owner: User, vendor: Path
) -> None:
    run = start(repository, owner, ref=None)

    assert run.execution_plan["job"]["skill_source"]["commit_sha"] == head(vendor)


def test_mcp_clients_inspect_then_submit(
    repository: Repository, owner: User, vendor: Path
) -> None:
    secret = token(owner, "run")

    inspected = call(secret, "inspect_skill", repository=REPOSITORY, path=PATH)
    assert not inspected.get("isError"), inspected
    commit = inspected["structuredContent"]["commit_sha"]
    submitted = call(
        secret,
        "submit_task",
        repository_id=repository.pk,
        definition={
            "name": "Triage",
            "skill": PATH,
            "skill_source": {"repository": REPOSITORY, "ref": commit},
        },
        idempotency_key="triage-mcp",
    )

    assert not submitted.get("isError"), submitted
    run = Run.objects.get()
    assert run.execution_plan["job"]["skill_source"]["commit_sha"] == commit
    assert "cannot run jobs" in error_text(
        call(token(owner, "read"), "inspect_skill", repository=REPOSITORY, path=PATH)
    )
