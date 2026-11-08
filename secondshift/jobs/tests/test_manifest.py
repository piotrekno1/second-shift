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


def test_each_job_setting_changes_its_hash() -> None:
    plain = parse_manifest(job_with("")).jobs["triage"]
    hashes = {
        manifest_hash(parse_manifest(job_with(setting)).jobs["triage"])
        for setting in (
            "",
            "    triggers: [webhook]",
            "    open_pull_request: true",
            "    open_issues: true",
            "    subagents: 2",
        )
    }

    assert len(hashes) == 5
    assert manifest_hash(plain) == manifest_hash(
        parse_manifest(job_with("")).jobs["triage"]
    )


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


def test_webhook_settings_change_the_hash() -> None:
    triggered = parse_manifest(job_with("    triggers: [webhook]")).jobs["triage"]
    with_settings = parse_manifest(job_with(WEBHOOK)).jobs["triage"]

    assert manifest_hash(with_settings) != manifest_hash(triggered)


def test_open_issues_takes_labels_and_a_limit() -> None:
    short = parse_manifest(job_with("    open_issues: true")).jobs["triage"]
    full = parse_manifest(
        job_with("    open_issues:\n      labels: [triage, ci]\n      max: 3")
    ).jobs["triage"]
    off = parse_manifest(job_with("    open_issues: false")).jobs["triage"]

    assert short.open_issues is not None
    assert (short.open_issues.labels, short.open_issues.max) == ([], 10)
    assert full.open_issues is not None
    assert (full.open_issues.labels, full.open_issues.max) == (["triage", "ci"], 3)
    assert off.open_issues is None
    assert manifest_hash(off) == manifest_hash(
        parse_manifest(job_with("")).jobs["triage"]
    )


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("    open_issues:\n      max: 11", "max"),
        ("    open_issues:\n      max: 0", "max"),
        ("    open_issues:\n      labels: [ci, CI]", "list each label once"),
        ("    open_issues:\n      labels: [' padded ']", "labels"),
        ("    open_issues:\n      assignees: [someone]", "assignees"),
    ],
)
def test_open_issues_refuses_bad_settings(extra: str, message: str) -> None:
    with pytest.raises(ManifestError) as error:
        parse_manifest(job_with(extra))

    assert message in str(error.value)


def test_subagents_cannot_pass_the_server_maximum(settings) -> None:
    settings.RUN_SUBAGENTS_MAX = 2

    assert parse_manifest(job_with("    subagents: 2")).jobs["triage"].subagents == 2
    with pytest.raises(ManifestError, match="subagents cannot be above 2"):
        parse_manifest(job_with("    subagents: 3"))
