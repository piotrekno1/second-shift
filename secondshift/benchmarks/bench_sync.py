"""Job reconciliation when a repository syncs its manifest."""

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from secondshift.benchmarks.bench_manifest import manifest_text
from secondshift.benchmarks.seed import make_workspace, query_budget
from secondshift.connections.models import Connection
from secondshift.jobs.manifest import RepositoryManifest, parse_manifest
from secondshift.jobs.models import Job, JobGrant
from secondshift.jobs.sync import sync_jobs
from secondshift.repositories.models import Repository

pytestmark = pytest.mark.django_db

JOBS = 50
# Sync reads once and writes in bulk, so neither count depends on JOBS.
FIRST_SYNC_QUERIES = 10
# An unchanged manifest writes nothing.
RESYNC_QUERIES = 7


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


def test_first_sync_with_automatic_grants(
    repository: Repository, manifest: RepositoryManifest
) -> None:
    """Approving without an owner happens in the same bulk writes."""
    tools = {
        "github": ["search_pull_requests", "list_commits"],
        "linear": ["list_issues"],
        "sentry": ["GET /issues"],
    }
    for connection in Connection.objects.filter(workspace=repository.workspace):
        connection.available_tools = tools.get(connection.binding_key, [])
        connection.metadata = {
            "base_url": "https://sentry.example.com",
            "settings": {"url": "https://mcp.example.com"},
        }
        connection.save()

    with query_budget(FIRST_SYNC_QUERIES):
        sync_jobs(repository, manifest)

    grants = JobGrant.objects.filter(capability_request__job__repository=repository)
    assert grants.count() == JOBS * 4
    assert not grants.exclude(status=JobGrant.Status.APPROVED).exists()
