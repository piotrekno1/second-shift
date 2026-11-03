"""Publishing a finished run's changes as a draft pull request."""

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from secondshift.config.constants import RETRY_PULL_REQUEST_WORKFLOW
from secondshift.repositories.models import Repository
from secondshift.repositories.pull_requests import (
    DraftPullRequest,
    PublishedPullRequest,
    PublishError,
)
from secondshift.runs import deliveries, publishing
from secondshift.runs.artifacts import artifact_storage, store_run_text
from secondshift.runs.models import Run, RunDelivery, RunEvent
from secondshift.runs.orchestration import workflows
from secondshift.runs.reporting.notifications import run_report_payload
from secondshift.runs.tests.test_collection import artifact_root  # noqa: F401
from secondshift.runs.tests.test_workflows import execution_run

REPORT = "## What changed\n\nREADME.md now says npm install."


@pytest.fixture
def run(db: object, artifact_root: Path) -> Run:  # noqa: F811
    """A finished run that changed one file and asked for a draft."""
    run = execution_run(db)
    Repository.objects.filter(pk=run.repository.pk).update(allow_pull_requests=True)
    run.status = Run.Status.SUCCEEDED
    run.summary = "Fixed the setup steps."
    run.finished_at = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    run.execution_plan["pull_request"] = {"base": "main", "run_ci": False}
    path = f"runs/{run.pk}/changes.patch"
    artifact_storage().save(path, io.BytesIO(b"diff --git a b\n"))
    run.outputs = {"changes": {"path": path, "file_count": 1, "files": ["README.md"]}}
    run.output_path = store_run_text(run, REPORT).path
    run.save()
    RunDelivery.objects.create(
        run=run, kind=RunDelivery.Kind.PULL_REQUEST, target="current/project into main"
    )
    return Run.objects.get(pk=run.pk)


class Publisher:
    def __init__(self, *results: PublishedPullRequest | Exception) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, bytes, DraftPullRequest]] = []

    def __call__(
        self, repository: Repository, commit: str, patch: Path, draft: Any
    ) -> PublishedPullRequest:
        self.calls.append((commit, patch.read_bytes(), draft))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


OPENED = PublishedPullRequest(12, "https://github.com/acme/web/pull/12")


def publisher(
    monkeypatch: pytest.MonkeyPatch, *results: PublishedPullRequest | Exception
) -> Publisher:
    fake = Publisher(*results)
    monkeypatch.setattr(publishing, "publish_pull_request", fake)
    return fake


def delivery(run: Run) -> RunDelivery:
    return RunDelivery.objects.get(run=run, kind=RunDelivery.Kind.PULL_REQUEST)


@pytest.mark.django_db
def test_the_stored_patch_and_report_become_a_draft(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = publisher(monkeypatch, OPENED)

    workflows.publish_pull_request_step(run.pk)

    [(commit, patch, draft)] = fake.calls
    assert commit == "a" * 40
    assert patch == b"diff --git a b\n"
    assert draft.branch == f"secondshift/run-{run.pk}"
    assert draft.base == "main"
    assert draft.title == "Review"
    assert draft.body.startswith(REPORT)
    assert f"run #{run.pk}" in draft.body
    assert draft.message.endswith("[skip ci]")
    assert draft.committed_at == run.finished_at
    published = delivery(run)
    assert published.status == RunDelivery.Status.DELIVERED
    assert published.external_url == OPENED.url
    assert published.message == "Opened draft #12 with changes to 1 file."
    assert RunEvent.objects.filter(run=run, message__startswith="Pull request").exists()
    payload = run_report_payload(run)
    assert payload["pull_request"] == {"url": OPENED.url}
    assert f"<{OPENED.url}|Review the draft pull request>" in payload["text"]


@pytest.mark.django_db
def test_trusted_repositories_run_ci_on_the_draft(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    run.execution_plan["pull_request"]["run_ci"] = True
    run.save()
    fake = publisher(monkeypatch, OPENED)

    workflows.publish_pull_request_step(run.pk)

    assert "[skip ci]" not in fake.calls[0][2].message


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"outputs": {"changes": {"file_count": 0}}}, "The agent made no changes."),
        ({"status": Run.Status.FAILED}, "The run did not succeed."),
        ({"outputs": {}}, "The run's changes could not be collected."),
        (
            {"outputs": {"changes": {"file_count": 900, "too_large": True}}},
            "The changes were larger than 20 MiB, so they were not kept.",
        ),
    ],
)
def test_runs_without_publishable_changes_are_skipped(
    run: Run, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any], reason: str
) -> None:
    Run.objects.filter(pk=run.pk).update(**change)
    fake = publisher(monkeypatch)

    workflows.publish_pull_request_step(run.pk)

    assert fake.calls == []
    assert delivery(run).status == RunDelivery.Status.SKIPPED
    assert delivery(run).message == reason


@pytest.mark.django_db
def test_an_owner_turning_pull_requests_off_stops_publishing(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    Repository.objects.filter(pk=run.repository.pk).update(allow_pull_requests=False)
    fake = publisher(monkeypatch)

    workflows.publish_pull_request_step(run.pk)

    assert fake.calls == []
    assert delivery(run).message == "The repository no longer allows pull requests."


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Mock:
    client = Mock()
    client.enqueue.side_effect = lambda options, *args: Mock(
        get_workflow_id=lambda: options["workflow_id"]
    )
    monkeypatch.setattr(deliveries, "new_dbos_client", lambda: client)
    return client


@pytest.mark.django_db
def test_a_failed_publish_is_retried_without_the_agent(
    run: Run, monkeypatch: pytest.MonkeyPatch, queue: Mock
) -> None:
    fake = publisher(monkeypatch, PublishError("GitHub refused the push."), OPENED)

    workflows.publish_pull_request_step(run.pk)
    failed = delivery(run)
    assert failed.status == RunDelivery.Status.FAILED
    assert failed.message == "GitHub refused the push."
    assert deliveries.retryable(failed)

    deliveries.retry_delivery(run, failed.pk)
    options, *args = queue.enqueue.call_args.args
    assert options["workflow_name"] == RETRY_PULL_REQUEST_WORKFLOW
    assert args == [run.pk]
    assert delivery(run).status == RunDelivery.Status.PENDING
    # What the retry workflow runs.
    workflows.publish_pull_request_step(run.pk)

    assert delivery(run).status == RunDelivery.Status.DELIVERED
    assert delivery(run).attempts == 2
    # Both attempts build the same draft, so the same commit is pushed.
    assert fake.calls[0][2] == fake.calls[1][2]

    workflows.publish_pull_request_step(run.pk)
    assert len(fake.calls) == 2


@pytest.mark.django_db
def test_unexpected_errors_fail_the_delivery_without_details(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher(monkeypatch, RuntimeError("token ghs_secret leaked in a trace"))

    workflows.publish_pull_request_step(run.pk)

    assert delivery(run).status == RunDelivery.Status.FAILED
    assert delivery(run).message == "Publishing failed unexpectedly."


@pytest.mark.django_db
def test_runs_publish_before_they_notify(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        workflows, "publish_pull_request_step", lambda run_id: order.append("publish")
    )
    monkeypatch.setattr(workflows, "notify", lambda run_id: order.append("notify"))

    workflows.deliver(run.pk)
    run.execution_plan["pull_request"] = None
    run.save()
    workflows.deliver(run.pk)

    assert order == ["publish", "notify", "notify"]
