from pathlib import Path

from secondshift.jobs.manifest import load_manifest

EXAMPLE = Path("examples/alert-triage")


def test_alert_triage_example_starts_from_a_webhook() -> None:
    job = load_manifest(EXAMPLE / ".secondshift.yaml").jobs["alert-triage"]

    assert job.triggers == ["webhook"]
    assert job.schedule is None
    assert job.webhook is not None
    assert job.webhook.skip_when == {"status": "resolved"}
    assert job.webhook.repeat_key == "groupKey"
    assert [(item.binding, item.required) for item in job.requires.mcps] == [
        ("grafana", False),
        ("sentry", False),
    ]
    assert (EXAMPLE / job.skill).is_file()


def test_root_symlink_finds_the_example_skill() -> None:
    link = Path(".agents/skills/alert-triage")

    assert link.is_symlink()
    assert (link / "SKILL.md").read_text() == (
        EXAMPLE / ".agents/skills/alert-triage/SKILL.md"
    ).read_text()


def test_skill_reads_the_payload_as_data() -> None:
    skill = (EXAMPLE / ".agents/skills/alert-triage/SKILL.md").read_text()

    assert "trigger_payload" in skill
    assert "untrusted" in skill
    assert "Not checked" in skill
