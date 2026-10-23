from pathlib import Path

import pytest
from django.db import transaction
from django.test import Client

from secondshift.automations import execution
from secondshift.automations.artifacts import (
    artifact_storage,
    store_run_input,
    store_run_text,
)
from secondshift.automations.execution import capture_run_input_step
from secondshift.automations.models import Run
from secondshift.automations.runner_protocol import CommandRequest, CommandResult
from secondshift.automations.tests.test_workflows import execution_run

SKILL = "# Review\n\nList the files and report.\n"


@pytest.fixture(params=["filesystem", "memory"])
def storage(request, settings, tmp_path: Path) -> str:
    # The in-memory backend has no local paths, like S3, so every read must go
    # through the storage API.
    backends = {
        "filesystem": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": tmp_path / "artifacts", "base_url": None},
        },
        "memory": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
    settings.STORAGES = {**settings.STORAGES, "run_artifacts": backends[request.param]}
    return request.param


class FakeRunner:
    def __init__(self, result: CommandResult | Exception) -> None:
        self.result = result
        self.requests: list[CommandRequest] = []

    def execute(self, session_id: str, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def stored_run(db: object) -> Run:
    run = execution_run(db)
    run.input_snapshot = {
        "job_key": "review",
        "skill_path": ".agents/skills/review/SKILL.md",
    }
    input_file = store_run_input(run, "# Input\n\nSkill text")
    output_file = store_run_text(run, "**Review report**\n\nAll good.")
    run.input_path, run.input_size_bytes = input_file.path, input_file.size_bytes
    run.output_path, run.output_size_bytes = output_file.path, output_file.size_bytes
    run.save()
    return run


@pytest.mark.django_db
def test_run_detail_lists_both_files(client: Client, storage: str, db) -> None:
    run = stored_run(db)
    client.force_login(run.job.workspace.owner)

    files = client.get(f"/api/runs/{run.pk}").json()["files"]

    assert [(f["name"], f["filename"], f["size_bytes"]) for f in files] == [
        ("input", f"run-{run.pk}-input.md", len("# Input\n\nSkill text")),
        ("output", f"run-{run.pk}-output.md", len("**Review report**\n\nAll good.")),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("input", b"# Input\n\nSkill text"),
        ("output", b"**Review report**\n\nAll good."),
    ],
)
def test_run_files_stream_from_any_storage_as_plain_text(
    client: Client, storage: str, db, name: str, content: bytes
) -> None:
    run = stored_run(db)
    client.force_login(run.job.workspace.owner)

    inline = client.get(f"/api/runs/{run.pk}/files/{name}")
    download = client.get(f"/api/runs/{run.pk}/files/{name}", {"download": "true"})

    assert b"".join(inline.streaming_content) == content
    assert inline["Content-Type"] == "text/plain; charset=utf-8"
    assert inline["X-Content-Type-Options"] == "nosniff"
    assert inline["Content-Disposition"].startswith("inline")
    assert download["Content-Disposition"] == (
        f'attachment; filename="run-{run.pk}-{name}.md"'
    )


@pytest.mark.django_db
def test_run_files_are_scoped_and_validated(client: Client, storage: str, db) -> None:
    run = stored_run(db)
    outsider = run.job.workspace.owner.__class__.objects.create_user(username="x")
    client.force_login(outsider)
    assert client.get(f"/api/runs/{run.pk}/files/output").status_code == 404

    client.force_login(run.job.workspace.owner)
    assert client.get(f"/api/runs/{run.pk}/files/secrets").status_code == 422
    Run.objects.filter(pk=run.pk).update(input_path="")
    assert client.get(f"/api/runs/{run.pk}/files/input").status_code == 404


@pytest.mark.django_db
def test_capture_stores_the_skill_read_from_the_sandbox(
    storage: str, db, monkeypatch
) -> None:
    run = execution_run(db)
    run.input_snapshot = {"job_key": "review", "skill_path": "skills/SKILL.md"}
    run.save(update_fields=["input_snapshot"])
    runner = FakeRunner(
        CommandResult(call_id="c", exit_code=0, timed_out=False, output=SKILL)
    )
    monkeypatch.setattr(execution, "get_runner_client", lambda: runner)

    capture_run_input_step(run.pk, "session-1")

    run.refresh_from_db()
    text = artifact_storage().open(run.input_path).read().decode()
    assert runner.requests[0].argv == ("cat", "--", "skills/SKILL.md")
    assert "## Skill: `skills/SKILL.md`" in text
    assert SKILL.strip() in text
    assert execution.task_prompt("skills/SKILL.md") in text
    assert run.input_size_bytes == len(text.encode())
    assert not run.events.filter(level="warning").exists()


@pytest.mark.django_db
def test_capture_still_stores_input_when_the_skill_is_unreadable(
    storage: str, db, monkeypatch
) -> None:
    run = execution_run(db)
    run.input_snapshot = {"job_key": "review", "skill_path": "skills/SKILL.md"}
    run.save(update_fields=["input_snapshot"])
    runner = FakeRunner(RuntimeError("runner down"))
    monkeypatch.setattr(execution, "get_runner_client", lambda: runner)

    capture_run_input_step(run.pk, "session-1")

    run.refresh_from_db()
    text = artifact_storage().open(run.input_path).read().decode()
    assert "could not read this file" in text
    assert run.events.filter(level="warning").count() == 1


@pytest.mark.django_db(transaction=True)
def test_deleting_a_run_removes_its_input_and_output(storage: str, db) -> None:
    run = stored_run(db)
    paths = [run.input_path, run.output_path]

    with transaction.atomic():
        run.delete()

    assert not any(artifact_storage().exists(path) for path in paths)


@pytest.mark.django_db
def test_run_list_flags_which_files_exist(client: Client, storage: str, db) -> None:
    run = stored_run(db)
    client.force_login(run.job.workspace.owner)

    item = client.get("/api/runs").json()["items"][0]

    assert (item["id"], item["has_input"], item["has_output"]) == (run.pk, True, True)
