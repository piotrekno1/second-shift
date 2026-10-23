from pathlib import Path

import pytest
from pydantic import ValidationError

from secondshift.automations.manifest import load_manifest


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

    with pytest.raises(ValidationError, match="repository-relative"):
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

    with pytest.raises(ValidationError, match="secrets"):
        load_manifest(path)
