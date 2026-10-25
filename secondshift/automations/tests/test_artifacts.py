import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from secondshift.automations.artifacts import (
    REDACTED,
    artifact_storage,
    delete_output,
    job_input_snapshot,
    output_path,
    read_bounded,
    read_output_excerpt,
    redact,
    store_run_output,
)
from secondshift.automations.models import Job, Run
from secondshift.automations.retention import prune_runs
from secondshift.core.services import create_workspace
from secondshift.repositories.models import Repository

SECRET = "hunter2-token+/="


@pytest.fixture
def artifact_root(settings, tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    settings.STORAGES = {
        **settings.STORAGES,
        "run_artifacts": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": root, "base_url": None},
        },
    }
    settings.RUN_WORK_ROOT = tmp_path / "work"
    return root


@pytest.fixture
def job(db) -> Job:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    repository = Repository.objects.create(
        workspace=workspace, full_name="example/project"
    )
    return Job.objects.create(
        workspace=workspace,
        repository=repository,
        key="review",
        name="Review",
        skill_path=".agents/skills/review/SKILL.md",
        cron="0 9 * * 1",
        config_hash="abc",
    )


def make_run(job: Job, **fields) -> Run:
    return Run.objects.create(job=job, scheduled_for=timezone.now(), **fields)


@pytest.mark.parametrize(
    "leaked",
    [
        SECRET,
        base64.b64encode(SECRET.encode()).decode(),
        base64.urlsafe_b64encode(SECRET.encode()).decode().rstrip("="),
        quote(SECRET, safe=""),
        json.dumps(SECRET)[1:-1],
    ],
)
def test_redact_catches_common_encodings(leaked: str) -> None:
    assert (
        redact(f"before {leaked} after", {"API_TOKEN": SECRET})
        == f"before {REDACTED} after"
    )


def test_read_bounded_keeps_head_and_tail(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.write_bytes(b"H" * 50 + b"M" * 900 + b"T" * 50)

    content, size, truncated = read_bounded(raw, max_bytes=100)

    assert (size, truncated) == (1000, True)
    assert content.startswith(b"H" * 50) and content.endswith(b"T" * 50)
    assert b"M" not in content
    assert b"900 bytes omitted" in content


def test_store_run_output_redacts_and_records_metadata(
    artifact_root, job, tmp_path
) -> None:
    run = make_run(job)
    raw = tmp_path / "raw"
    raw.write_text(f"token={SECRET}\ndone\n")

    stored = store_run_output(run, raw, {"API_TOKEN": SECRET})

    saved = artifact_storage().open(stored.path).read().decode()
    assert SECRET not in saved and REDACTED in saved
    assert stored.size_bytes == raw.stat().st_size
    assert not stored.truncated
    assert read_output_excerpt(stored.path).endswith("done\n")


def test_job_input_snapshot_freezes_job_config(job) -> None:
    snapshot = job_input_snapshot(job)
    job.skill_path = ".agents/skills/later/SKILL.md"

    assert snapshot["skill_path"] == ".agents/skills/review/SKILL.md"
    assert snapshot["config_hash"] == "abc"
    assert "secret_names" not in snapshot


class FakeDBOSClient:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_workflows(
        self, workflow_ids: list[str], *, delete_children: bool
    ) -> None:
        self.deleted.extend(workflow_ids)


def test_prune_runs_removes_old_finished_runs_everywhere(
    artifact_root, job, tmp_path, django_capture_on_commit_callbacks
) -> None:
    old = timezone.now() - timedelta(days=31)
    expired = make_run(
        job, status=Run.Status.FAILED, finished_at=old, dbos_workflow_id="wf-old"
    )
    raw = tmp_path / "raw"
    raw.write_text("old output")
    expired.output_path = store_run_output(expired, raw, {}).path
    expired.save()
    recent = make_run(job, status=Run.Status.SUCCEEDED, finished_at=timezone.now())
    running = Run.objects.create(
        job=job,
        scheduled_for=old,
        status=Run.Status.RUNNING,
        dbos_workflow_id="wf-running",
    )
    client = FakeDBOSClient()

    with django_capture_on_commit_callbacks(execute=True):
        assert prune_runs(days=30, client=client) == 1

    assert client.deleted == ["wf-old"]
    assert not artifact_storage().exists(expired.output_path)
    assert set(Run.objects.values_list("pk", flat=True)) == {recent.pk, running.pk}


def test_prune_runs_disabled_with_zero_days(job) -> None:
    make_run(
        job, status=Run.Status.FAILED, finished_at=timezone.now() - timedelta(days=999)
    )
    assert prune_runs(days=0) == 0
    assert Run.objects.count() == 1


def test_output_path_partitions_by_workspace_and_day(job) -> None:
    run = make_run(job)
    run.created_at = datetime(2026, 3, 9, 23, 30, tzinfo=UTC)

    assert output_path(run) == f"runs/{job.workspace_id}/2026/03/09/{run.pk}/output.log"


def test_deleting_artifacts_removes_emptied_partitions(
    artifact_root, job, tmp_path
) -> None:
    kept, removed = make_run(job), make_run(job)
    raw = tmp_path / "raw"
    raw.write_text("output")
    for run in (kept, removed):
        run.output_path = store_run_output(run, raw, {}).path

    delete_output(removed.output_path)

    assert not (artifact_root / Path(removed.output_path).parent).exists()
    assert artifact_storage().exists(kept.output_path)
    delete_output(kept.output_path)
    assert list(artifact_root.iterdir()) == []


def stored_run(job: Job, tmp_path: Path) -> Run:
    run = make_run(job)
    raw = tmp_path / f"raw-{run.pk}"
    raw.write_text("leaked output")
    run.output_path = store_run_output(run, raw, {}).path
    run.save()
    return run


def test_deleting_a_run_deletes_its_output_after_commit(
    artifact_root, job, tmp_path, django_capture_on_commit_callbacks
) -> None:
    run = stored_run(job, tmp_path)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        run.delete()
    assert artifact_storage().exists(run.output_path)  # not before commit

    for callback in callbacks:
        callback()
    assert not artifact_storage().exists(run.output_path)


def test_queryset_delete_deletes_each_output(
    artifact_root, job, tmp_path, django_capture_on_commit_callbacks
) -> None:
    paths = [stored_run(job, tmp_path).output_path for _ in range(2)]

    with django_capture_on_commit_callbacks(execute=True):
        Run.objects.all().delete()

    assert not any(artifact_storage().exists(path) for path in paths)


def test_rolled_back_delete_keeps_output(artifact_root, job, tmp_path) -> None:
    run = stored_run(job, tmp_path)
    run_id = run.pk

    with pytest.raises(RuntimeError), transaction.atomic():
        run.delete()
        raise RuntimeError("abort")

    assert Run.objects.filter(pk=run_id).exists()
    assert artifact_storage().exists(run.output_path)
