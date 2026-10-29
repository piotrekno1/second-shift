"""Manifest parsing. Runs on every repository sync and every check_manifest call."""

import yaml
from pytest_benchmark.fixture import BenchmarkFixture

from secondshift.jobs.manifest import manifest_hash, parse_manifest


def manifest_text(jobs: int) -> str:
    return yaml.safe_dump(
        {
            "version": 1,
            "jobs": {
                f"job-{index}": {
                    "name": f"Job {index}",
                    "skill": f".agents/skills/job-{index}/SKILL.md",
                    "schedule": "0 9 * * 1-5",
                    "timezone": "Europe/Warsaw",
                    "history": 2,
                    "requires": {
                        "mcps": [
                            {
                                "binding": "github",
                                "tools": ["search_pull_requests", "list_commits"],
                            },
                            {"binding": "linear", "tools": ["list_issues"]},
                        ],
                        "apis": [{"binding": "sentry", "tools": ["GET /issues"]}],
                    },
                    "notify": [{"binding": "engineering-alerts"}],
                }
                for index in range(jobs)
            },
        }
    )


def test_parse_small_manifest(benchmark: BenchmarkFixture) -> None:
    text = manifest_text(4)

    manifest = benchmark(parse_manifest, text)

    assert len(manifest.jobs) == 4


def test_parse_large_manifest(benchmark: BenchmarkFixture) -> None:
    text = manifest_text(200)

    manifest = benchmark(parse_manifest, text)

    assert len(manifest.jobs) == 200


def test_hash_every_job(benchmark: BenchmarkFixture) -> None:
    jobs = list(parse_manifest(manifest_text(200)).jobs.values())

    hashes = benchmark(lambda: [manifest_hash(job) for job in jobs])

    assert len(set(hashes)) == 200
