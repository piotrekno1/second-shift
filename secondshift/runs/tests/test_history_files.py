import tarfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone
from pydantic_ai import ModelRetry

from secondshift.jobs.models import Job
from secondshift.repositories.models import Repository
from secondshift.repositories.sources import SourceSnapshot
from secondshift.runs import history
from secondshift.runs.artifacts import artifact_storage, saved_file_path
from secondshift.runs.execution.run import run_prompt
from secondshift.runs.execution.workspace_tools import readable_path
from secondshift.runs.history import pack_earlier_files
from secondshift.runs.models import Run
from secondshift.runs.orchestration import workflows
from secondshift.runs.services import create_run
from secondshift.runs.tests.test_workflows import FakeRunnerClient
from secondshift.workspaces.dao import create_workspace


@pytest.fixture(autouse=True)
def artifact_root(settings, tmp_path: Path) -> None:
    settings.STORAGES = {
        **settings.STORAGES,
        "run_artifacts": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": tmp_path / "artifacts", "base_url": None},
        },
    }


@pytest.fixture
def repository(db) -> Repository:
    user = get_user_model().objects.create_user(username="owner")
    workspace = create_workspace(name="Test", slug="test", owner=user)
    return Repository.objects.create(
        workspace=workspace, full_name="example/project", config_sha="a" * 40
    )


def make_job(repository: Repository, key: str = "audit", history: int = 2) -> Job:
    return Job.objects.create(
        workspace=repository.workspace,
        repository=repository,
        key=key,
        name=key,
        skill_path=".agents/skills/audit/SKILL.md",
        config_hash="abc",
        history=history,
    )


def run_with_files(job: Job, files: dict[str, bytes], days_ago: int = 1) -> Run:
    """A successful earlier run that saved these files in /outputs."""
    run = create_run(job, timezone.now() - timedelta(days=days_ago))
    storage = artifact_storage()
    saved = []
    for name, content in files.items():
        path = storage.save(saved_file_path(run, name), ContentFile(content))
        saved.append({"name": name, "path": path, "size_bytes": len(content)})
    run.status = Run.Status.SUCCEEDED
    run.outputs = {"files": saved}
    run.save(update_fields=["status", "outputs"])
    return run


def members(archive: Path) -> dict[str, bytes]:
    with tarfile.open(archive) as opened:
        return {
            member.name: opened.extractfile(member).read()  # type: ignore[union-attr]
            for member in opened
        }


def test_packs_the_history_runs_files_by_run(repository, tmp_path: Path) -> None:
    job = make_job(repository, history=2)
    run_with_files(job, {"oldest.json": b"0"}, days_ago=3)
    middle = run_with_files(job, {"ledger.json": b"{}"}, days_ago=2)
    newest = run_with_files(job, {"findings.json": b"[]", "a/b.csv": b"x"})
    run_with_files(make_job(repository, key="other"), {"secret.txt": b"no"})
    run = create_run(job, timezone.now())

    packed = pack_earlier_files(run, tmp_path / "history.tar.gz")

    assert packed is not None
    assert members(tmp_path / "history.tar.gz") == {
        f"{newest.pk}/findings.json": b"[]",
        f"{newest.pk}/a/b.csv": b"x",
        f"{middle.pk}/ledger.json": b"{}",
    }
    assert packed.record == {
        "files": [
            {"path": f"/history/{newest.pk}/findings.json", "size_bytes": 2},
            {"path": f"/history/{newest.pk}/a/b.csv", "size_bytes": 1},
            {"path": f"/history/{middle.pk}/ledger.json", "size_bytes": 2},
        ],
        "left_out": 0,
    }


def test_the_same_files_give_the_same_archive(repository, tmp_path: Path) -> None:
    job = make_job(repository)
    run_with_files(job, {"findings.json": b"[]"})
    run = create_run(job, timezone.now())

    first = pack_earlier_files(run, tmp_path / "first.tar.gz")
    second = pack_earlier_files(run, tmp_path / "second.tar.gz")

    assert first is not None and second is not None
    assert first.sha256 == second.sha256


def test_newest_files_fill_the_size_limit_first(
    repository, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(history, "MAX_HISTORY_FILES_BYTES", 10)
    job = make_job(repository)
    run_with_files(job, {"old.txt": b"123456"}, days_ago=2)
    newest = run_with_files(job, {"new.txt": b"12345678"})
    run = create_run(job, timezone.now())

    packed = pack_earlier_files(run, tmp_path / "history.tar.gz")

    assert packed is not None
    assert list(members(tmp_path / "history.tar.gz")) == [f"{newest.pk}/new.txt"]
    assert packed.record["left_out"] == 1


def test_a_removed_or_unsafe_file_is_skipped(repository, tmp_path: Path) -> None:
    job = make_job(repository)
    earlier = run_with_files(job, {"kept.txt": b"k", "gone.txt": b"g"})
    artifact_storage().delete(earlier.outputs["files"][1]["path"])
    earlier.outputs["files"].append(
        {"name": "../escape", "path": earlier.outputs["files"][0]["path"]}
    )
    earlier.save(update_fields=["outputs"])
    run = create_run(job, timezone.now())

    pack_earlier_files(run, tmp_path / "history.tar.gz")

    assert list(members(tmp_path / "history.tar.gz")) == [f"{earlier.pk}/kept.txt"]


def test_without_history_or_files_nothing_is_packed(repository, tmp_path: Path) -> None:
    run_with_files(make_job(repository, key="off", history=0), {"a.txt": b"a"})
    plain = make_job(repository, key="plain")
    run_with_files(plain, {})

    for job in Job.objects.filter(key__in=["off", "plain"]):
        run = create_run(job, timezone.now())
        assert pack_earlier_files(run, tmp_path / "history.tar.gz") is None


def test_the_prompt_lists_the_packed_files(repository) -> None:
    job = make_job(repository)
    earlier = run_with_files(job, {"findings.json": b"[]"})
    run = create_run(job, timezone.now())
    assert "/history" not in run_prompt(run)

    run.input_snapshot["history_files"] = {
        "files": [{"path": f"/history/{earlier.pk}/findings.json", "size_bytes": 2}],
        "left_out": 3,
    }

    prompt = run_prompt(run)
    assert f"- /history/{earlier.pk}/findings.json (2 bytes)" in prompt
    assert "3 older files were left out" in prompt


@pytest.mark.django_db
def test_the_session_mounts_the_packed_files(repository, monkeypatch) -> None:
    job = make_job(repository)
    earlier = run_with_files(job, {"findings.json": b"[]"})
    run = create_run(
        job,
        timezone.now(),
        execution_plan={
            "ready": True,
            "repository": {"full_name": "example/project", "config_sha": "a" * 40},
            "runner": {"image": "server-owned:test", "network_policy": "none"},
        },
    )
    client = FakeRunnerClient()
    monkeypatch.setattr(workflows, "get_runner_client", lambda: client)
    monkeypatch.setattr(
        workflows,
        "source_snapshot",
        lambda *args: SourceSnapshot(Path("/cache/a.tar.gz"), "e" * 64, 1),
    )

    workflows.start_runner_session_step(run.pk)

    history_sha256 = client.started[0].history_sha256
    assert [sha for sha, _ in client.uploaded] == ["e" * 64, history_sha256]
    run.refresh_from_db()
    assert run.input_snapshot["history_files"]["files"] == [
        {"path": f"/history/{earlier.pk}/findings.json", "size_bytes": 2}
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/history/41/findings.json", "/history/41/findings.json"),
        ("/history", "/history"),
        ("src/app.py", "src/app.py"),
    ],
)
def test_file_tools_read_the_repository_and_history(path: str, expected: str) -> None:
    assert readable_path(path) == expected


@pytest.mark.parametrize(
    "path", ["/history/../etc/passwd", "/etc/passwd", "/historyx/a", "../secret"]
)
def test_file_tools_refuse_other_paths(path: str) -> None:
    with pytest.raises(ModelRetry):
        readable_path(path)
