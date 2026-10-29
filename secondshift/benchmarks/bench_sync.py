"""Job reconciliation when a repository syncs its manifest."""

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from secondshift.benchmarks.bench_manifest import manifest_text
from secondshift.benchmarks.seed import make_workspace, query_budget
from secondshift.connections.models import Connection
from secondshift.jobs.manifest import RepositoryManifest, parse_manifest
from secondshift.jobs.models import Job
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.models import Repository

pytestmark = pytest.mark.django_db

JOBS = 50
# Sync runs a few queries for each job and each capability it requests, so
# the count grows with the manifest. These budgets hold today's cost per job.
# Lower them when sync starts batching its writes.
FIRST_SYNC_QUERIES = 49 * JOBS
RESYNC_QUERIES = 34 * JOBS


@pytest.fixture
def repository() -> Repository:
    workspace = make_workspace()
    for binding, kind in (
        ("github", Connection.Kind.MCP),
        ("linear", Connection.Kind.MCP),
        ("sentry", Connection.Kind.API),
        ("engineering-alerts", Connection.Kind.NOTIFICATION),
    ):
        Connection.objects.create(
            workspace=workspace,
            provider=Connection.Provider.MCP,
            kind=kind,
            binding_key=binding,
            display_name=binding,
        )
    return Repository.objects.create(workspace=workspace, full_name="acme/api")


@pytest.fixture
def manifest() -> RepositoryManifest:
    return parse_manifest(manifest_text(JOBS))


def test_first_sync(
    benchmark: BenchmarkFixture, repository: Repository, manifest: RepositoryManifest
) -> None:
    with query_budget(FIRST_SYNC_QUERIES):
        sync_jobs(repository, manifest)

    def clear() -> None:
        Job.objects.filter(repository=repository).delete()

    benchmark.pedantic(sync_jobs, args=(repository, manifest), setup=clear, rounds=10)

    assert Job.objects.filter(repository=repository).count() == JOBS


def test_resync_without_changes(
    benchmark: BenchmarkFixture, repository: Repository, manifest: RepositoryManifest
) -> None:
    sync_jobs(repository, manifest)

    with query_budget(RESYNC_QUERIES):
        sync_jobs(repository, manifest)
    jobs = benchmark(sync_jobs, repository, manifest)

    assert len(jobs) == JOBS
