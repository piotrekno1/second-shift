from pathlib import Path

import pytest
from django.utils import timezone

from secondshift.jobs import repository_sync
from secondshift.jobs.dao import disable_repository_jobs
from secondshift.jobs.repository_sync import (
    UNEXPECTED_SYNC_ERROR,
    attach_local_repository,
    run_requested_syncs,
)
from secondshift.repositories.dao import request_sync
from secondshift.repositories.models import Repository
from secondshift.repositories.tests.test_autosync import rename_job
from secondshift.repositories.tests.test_local import create_local_repository
from secondshift.workspaces.models import Workspace


@pytest.fixture
def repository(workspace: Workspace, tmp_path: Path) -> Repository:
    create_local_repository(tmp_path / "project")
    return attach_local_repository(workspace, str(tmp_path / "project"))


def test_a_second_request_joins_the_waiting_sync(
    repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    syncs = []

    def sync(row: Repository) -> Repository:
        syncs.append(row)
        return row

    monkeypatch.setattr(repository_sync, "sync_repository", sync)

    request_sync(repository.pk)
    request_sync(repository.pk)
    run_requested_syncs()

    assert syncs == [repository]
    repository.refresh_from_db()
    assert repository.sync_requested_at is None


def test_a_request_made_during_a_sync_runs_after_it(
    repository: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync = repository_sync.sync_repository
    names = []

    def sync_then_commit(row: Repository) -> Repository:
        synced = sync(row)
        names.append(synced.jobs.get().name)
        if len(names) == 1:
            # The sync already read the branch. This commit needs another.
            rename_job(tmp_path / "project", "Weekly review")
            request_sync(row.pk)
        return synced

    monkeypatch.setattr(repository_sync, "sync_repository", sync_then_commit)
    request_sync(repository.pk)

    run_requested_syncs()
    run_requested_syncs()

    assert names == ["Review", "Weekly review"]
    repository.refresh_from_db()
    assert repository.sync_requested_at is None


def test_an_unexpected_failure_is_shown_and_ends_the_request(
    repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(row: Repository) -> Repository:
        raise RuntimeError("boom")

    monkeypatch.setattr(repository_sync, "sync_repository", crash)
    request_sync(repository.pk)

    assert run_requested_syncs() == 0

    repository.refresh_from_db()
    assert repository.sync_requested_at is None
    assert repository.last_sync_error == UNEXPECTED_SYNC_ERROR


def test_a_removal_during_a_sync_stands(
    repository: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rename_job(tmp_path / "project", "Weekly review")
    read = repository_sync.read_committed_config

    def read_then_remove(*args: object) -> object:
        # The owner removes the repository after the worker read the branch.
        config = read(*args)  # type: ignore[arg-type]
        Repository.objects.filter(pk=repository.pk).update(
            enabled=False, removed_at=timezone.now()
        )
        disable_repository_jobs(repository)
        return config

    monkeypatch.setattr(repository_sync, "read_committed_config", read_then_remove)
    request_sync(repository.pk)
    run_requested_syncs()

    repository.refresh_from_db()
    assert repository.removed_at is not None
    assert repository.enabled is False
    job = repository.jobs.get()
    assert (job.name, job.enabled) == ("Review", False)
