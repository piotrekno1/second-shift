import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from secondshift.runner.collection import OUTPUTS_DIR, collect, record_baseline
from secondshift.runs.artifacts import (
    CollectionError,
    artifact_storage,
    store_collection,
)
from secondshift.runs.models import Run, RunEvent
from secondshift.runs.orchestration import workflows
from secondshift.runs.outputs import RunOutputs
from secondshift.runs.reporting.collection import collection_summary
from secondshift.runs.tests.test_workflows import execution_run
from secondshift.workspaces.dao import workspace_owner


@pytest.fixture
def artifact_root(settings: Any, tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    settings.STORAGES = {
        **settings.STORAGES,
        "run_artifacts": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": root, "base_url": None},
        },
    }
    return root


def agent_session(tmp_path: Path) -> Path:
    """A session where the agent edited a file and saved a report file."""
    root = tmp_path / "session"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (root / OUTPUTS_DIR).mkdir()
    (workspace / "README.md").write_text("old\n")
    record_baseline(root, workspace)
    (workspace / "README.md").write_text("new\n")
    (root / OUTPUTS_DIR / "findings.csv").write_text("file,issue\nREADME.md,stale\n")
    return collect(root, workspace)


def archive_with(tmp_path: Path, *members: tarfile.TarInfo) -> Path:
    path = tmp_path / "crafted.tar.gz"
    with tarfile.open(path, mode="w:gz") as archive:
        for member in members:
            content = b"x" * member.size if member.isreg() else None
            archive.addfile(member, io.BytesIO(content) if content else None)
    return path


def regular(name: str, size: int = 1) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.size = size
    return member


@pytest.mark.django_db
def test_collected_changes_and_files_are_stored_with_the_run(
    db: object, artifact_root: Path, tmp_path: Path
) -> None:
    run = execution_run(db)

    outputs = store_collection(run, agent_session(tmp_path))

    changes = outputs.changes
    assert changes.file_count == 1
    assert changes.files == ("README.md",)
    assert changes.path.endswith(f"/{run.pk}/changes.patch")
    assert b"+new" in artifact_storage().open(changes.path).read()
    [saved] = outputs.files
    assert saved.name == "findings.csv"
    assert saved.path.endswith(f"/{run.pk}/outputs/findings.csv")
    assert saved.size_bytes == len("file,issue\nREADME.md,stale\n")
    assert len(saved.sha256) == 64
    assert outputs.skipped == ()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "member",
    [
        regular("outputs/../../escape.txt"),
        regular("/etc/passwd"),
        regular("unexpected.txt"),
        regular("outputs/"),
    ],
)
def test_archives_with_unexpected_names_are_refused(
    db: object, artifact_root: Path, tmp_path: Path, member: tarfile.TarInfo
) -> None:
    run = execution_run(db)

    with pytest.raises(CollectionError):
        store_collection(run, archive_with(tmp_path, member))

    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.django_db
def test_archives_with_links_are_refused(
    db: object, artifact_root: Path, tmp_path: Path
) -> None:
    run = execution_run(db)
    link = tarfile.TarInfo("outputs/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"

    with pytest.raises(CollectionError):
        store_collection(run, archive_with(tmp_path, link))


@pytest.mark.django_db
def test_deleting_a_run_deletes_its_collected_files(
    db: object,
    artifact_root: Path,
    tmp_path: Path,
    django_capture_on_commit_callbacks: Any,
) -> None:
    run = execution_run(db)
    outputs = store_collection(run, agent_session(tmp_path))
    run.outputs = outputs.stored()
    run.save()
    paths = [outputs.changes.path, outputs.files[0].path]
    assert all(artifact_storage().exists(path) for path in paths)

    with django_capture_on_commit_callbacks(execute=True):
        run.delete()

    assert not any(artifact_storage().exists(path) for path in paths)


class CollectingRunner:
    def __init__(self, archive: Path | None) -> None:
        self.archive = archive
        self.collected: list[str] = []

    def collect(self, session_id: str, destination: Path) -> None:
        self.collected.append(session_id)
        if self.archive is None:
            raise RuntimeError("The session has ended, so its files are gone.")
        destination.write_bytes(self.archive.read_bytes())


@pytest.mark.django_db
def test_the_workflow_keeps_what_the_agent_left_behind(
    db: object,
    artifact_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = execution_run(db)
    runner = CollectingRunner(agent_session(tmp_path))
    monkeypatch.setattr(workflows, "get_runner_client", lambda: runner)

    workflows.collect_outputs(run.pk, "run-session")

    run.refresh_from_db()
    assert runner.collected == ["run-session"]
    assert run.outputs["files"][0]["name"] == "findings.csv"
    assert RunEvent.objects.get(run=run).message == (
        "Collected changes to 1 file and 1 saved file."
    )


@pytest.mark.django_db
def test_a_failed_collection_is_noted_and_keeps_the_run(
    db: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = execution_run(db)
    monkeypatch.setattr(workflows, "get_runner_client", lambda: CollectingRunner(None))

    workflows.collect_outputs(run.pk, "run-session")

    run.refresh_from_db()
    assert run.outputs == {}
    assert run.status == Run.Status.QUEUED
    event = RunEvent.objects.get(run=run)
    assert event.level == "warning"
    assert "could not be collected" in event.message


@pytest.mark.parametrize(
    ("outputs", "summary"),
    [
        ({}, ""),
        ({"changes": {"file_count": 0}, "files": [], "skipped": []}, ""),
        (
            {"changes": {"file_count": 3, "path": "p"}, "files": []},
            "Collected changes to 3 files.",
        ),
        (
            {
                "changes": {"file_count": 0},
                "files": [
                    {"name": "a.csv", "path": "a", "size_bytes": 1},
                    {"name": "b.csv", "path": "b", "size_bytes": 1},
                ],
                "skipped": [{"name": "link", "reason": "Not a regular file."}],
            },
            "Collected 2 saved files. Skipped 1 saved file.",
        ),
        (
            {"changes": {"file_count": 900, "too_large": True}, "files": []},
            "Changes to 900 files were larger than 20 MiB, so they were not kept.",
        ),
    ],
)
def test_collection_summary(outputs: dict[str, Any], summary: str) -> None:
    assert collection_summary(RunOutputs.model_validate(outputs)) == summary


@pytest.mark.django_db
def test_collected_files_are_listed_and_downloaded_safely(
    client: Any, db: object, artifact_root: Path, tmp_path: Path
) -> None:
    run = execution_run(db)
    archive = agent_session(tmp_path)
    run.outputs = store_collection(run, archive).stored()
    run.outputs["skipped"] = [{"name": "link", "reason": "Not a regular file."}]
    run.save()
    client.force_login(workspace_owner(run.job.workspace))

    detail = client.get(f"/api/runs/{run.pk}").json()
    patch = client.get(f"/api/runs/{run.pk}/changes")
    saved = client.get(f"/api/runs/{run.pk}/saved-files/findings.csv")

    assert detail["changes"] == {
        "file_count": 1,
        "files": ["README.md"],
        "too_large": False,
        "available": True,
        "size_bytes": run.outputs["changes"]["size_bytes"],
    }
    assert detail["saved_files"] == [
        {"name": "findings.csv", "size_bytes": len("file,issue\nREADME.md,stale\n")}
    ]
    assert detail["skipped_files"] == [
        {"name": "link", "reason": "Not a regular file."}
    ]
    assert patch["Content-Type"] == "text/plain; charset=utf-8"
    assert b"+new" in b"".join(patch.streaming_content)
    assert saved["Content-Type"] == "application/octet-stream"
    assert saved["Content-Disposition"] == 'attachment; filename="findings.csv"'
    assert saved["X-Content-Type-Options"] == "nosniff"
    assert b"".join(saved.streaming_content) == b"file,issue\nREADME.md,stale\n"
    for name in ("missing.csv", "../changes.patch", "outputs/findings.csv"):
        assert client.get(f"/api/runs/{run.pk}/saved-files/{name}").status_code == 404


@pytest.mark.django_db
def test_runs_without_collected_files_show_none(
    client: Any, db: object, artifact_root: Path
) -> None:
    run = execution_run(db)
    client.force_login(workspace_owner(run.job.workspace))

    detail = client.get(f"/api/runs/{run.pk}").json()

    assert detail["changes"] is None
    assert detail["saved_files"] == []
    assert client.get(f"/api/runs/{run.pk}/changes").status_code == 404
