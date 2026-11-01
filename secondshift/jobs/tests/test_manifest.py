import hashlib
import json
from pathlib import Path

import pytest

from secondshift.jobs.manifest import (
    ManifestError,
    load_manifest,
    manifest_hash,
    parse_manifest,
)


def write_manifest(path: Path, skill: str = ".agents/skills/review/SKILL.md") -> None:
    path.write_text(
        f"""
version: 1
jobs:
  weekly-review:
    name: Weekly review
    skill: {skill}
    schedule: "0 9 * * 1"
    timezone: Europe/Warsaw
    requires:
      mcps:
        - binding: observability
          tools: [search_dashboards, query_metrics]
          required: true
    notify:
      - binding: engineering-alerts
""".strip()
    )


def test_load_manifest_accepts_repository_relative_skill(tmp_path: Path) -> None:
    path = tmp_path / ".secondshift.yaml"
    write_manifest(path)

    manifest = load_manifest(path)

    assert manifest.jobs["weekly-review"].timezone == "Europe/Warsaw"
    job = manifest.jobs["weekly-review"]
    assert job.requires.mcps[0].binding == "observability"
    assert job.requires.mcps[0].tools == ["search_dashboards", "query_metrics"]
    assert job.notify[0].binding == "engineering-alerts"


def test_load_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    path = tmp_path / ".secondshift.yaml"
    write_manifest(path, "../outside/SKILL.md")

    with pytest.raises(ManifestError, match="repository-relative"):
        load_manifest(path)


def test_load_manifest_rejects_credentials_and_endpoints(tmp_path: Path) -> None:
    path = tmp_path / ".secondshift.yaml"
    path.write_text(
        """
version: 1
jobs:
  review:
    name: Review
    skill: .agents/skills/review/SKILL.md
    schedule: "0 9 * * 1"
    secrets: [GRAFANA_TOKEN]
""".strip()
    )

    with pytest.raises(ManifestError, match=r"Unknown field: jobs\.review\.secrets\."):
        load_manifest(path)


def test_parse_manifest_names_unknown_fields_once() -> None:
    text = """
jobs:
  first:
    name: First
    skill: .agents/skills/first/SKILL.md
    retries: 1
  second:
    name: Second
    skill: .agents/skills/second/SKILL.md
    retries: 1
""".strip()

    with pytest.raises(ManifestError) as error:
        parse_manifest(text)

    assert str(error.value) == (
        "Unknown fields: jobs.first.retries, jobs.second.retries. Check the "
        "spelling, or update SecondShift if the file was written for a newer version."
    )


def test_parse_manifest_reports_each_problem_plainly() -> None:
    text = """
jobs:
  review:
    skill: .agents/skills/review/SKILL.md
    schedule: every monday
""".strip()

    with pytest.raises(ManifestError) as error:
        parse_manifest(text)

    assert str(error.value) == (
        "jobs.review.name is required. jobs.review.schedule: schedule must be "
        "a valid five-field cron expression."
    )


def job_with(extra: str) -> str:
    return f"""
version: 1
jobs:
  triage:
    name: Triage
    skill: .agents/skills/triage/SKILL.md
{extra}
""".strip()


def test_manifest_names_a_webhook_trigger() -> None:
    manifest = parse_manifest(job_with("    triggers: [webhook]"))

    assert manifest.jobs["triage"].triggers == ["webhook"]


@pytest.mark.parametrize(
    "extra",
    [
        "    triggers: [email]",
        "    triggers: [webhook, webhook]",
        "    triggers:\n      - url: https://example.com",
    ],
)
def test_manifest_refuses_unknown_or_repeated_triggers(extra: str) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(job_with(extra))


def test_jobs_without_triggers_keep_their_hash() -> None:
    plain = parse_manifest(job_with("")).jobs["triage"]
    triggered = parse_manifest(job_with("    triggers: [webhook]")).jobs["triage"]
    before_triggers = plain.model_dump(exclude={"triggers", "webhook"})
    canonical = json.dumps(before_triggers, sort_keys=True, separators=(",", ":"))

    assert manifest_hash(plain) == hashlib.sha256(canonical.encode()).hexdigest()
    assert manifest_hash(triggered) != manifest_hash(plain)


WEBHOOK = """    triggers: [webhook]
    webhook:
      skip_when:
        status: resolved
      repeat_key: groupKey
      repeat_window: 2h"""


def test_manifest_reads_webhook_settings() -> None:
    webhook = parse_manifest(job_with(WEBHOOK)).jobs["triage"].webhook

    assert webhook is not None
    assert webhook.skip_when == {"status": "resolved"}
    assert (webhook.repeat_key, webhook.repeat_window) == ("groupKey", "2h")


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("    webhook:\n      repeat_key: groupKey", "need triggers: [webhook]"),
        (
            "    triggers: [webhook]\n    webhook:\n      repeat_window: 1h",
            "repeat_window needs a repeat_key",
        ),
        (
            "    triggers: [webhook]\n    webhook:\n      repeat_key: a\n"
            "      repeat_window: 8d",
            "between 1m and 7d",
        ),
        (
            "    triggers: [webhook]\n    webhook:\n      repeat_key: a\n"
            "      repeat_window: soon",
            "followed by m, h or d",
        ),
        (
            "    triggers: [webhook]\n    webhook:\n      repeat_key: alerts[0]",
            "repeat_key",
        ),
    ],
)
def test_manifest_refuses_bad_webhook_settings(extra: str, message: str) -> None:
    with pytest.raises(ManifestError) as error:
        parse_manifest(job_with(extra))

    assert message in str(error.value)


def test_jobs_without_webhook_settings_keep_their_hash() -> None:
    triggered = parse_manifest(job_with("    triggers: [webhook]")).jobs["triage"]
    canonical = json.dumps(
        triggered.model_dump(exclude={"webhook"}), sort_keys=True, separators=(",", ":")
    )

    assert manifest_hash(triggered) == hashlib.sha256(canonical.encode()).hexdigest()
    with_settings = parse_manifest(job_with(WEBHOOK)).jobs["triage"]
    assert manifest_hash(with_settings) != manifest_hash(triggered)
