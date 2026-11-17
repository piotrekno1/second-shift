import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from secondshift.agent import build as agent_build
from secondshift.agent.build import AGENT_IMAGE_DIR, build_agent_image
from secondshift.docker import commands


def test_agent_image_builds_from_only_the_packaged_agent_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id = "sha256:" + "b" * 64
    seen: dict[str, object] = {}

    def run(argv: list[str], **kwargs: object) -> Mock:
        if argv[1] == "build":
            context = Path(argv[-1])
            seen["build"] = argv
            seen["files"] = sorted(
                str(path.relative_to(context))
                for path in context.rglob("*")
                if path.is_file()
            )
        return Mock(returncode=0, stdout=image_id)

    monkeypatch.setattr(commands.subprocess, "run", run)

    assert build_agent_image() == image_id
    assert seen["files"] == [
        "secondshift/agent/image/Dockerfile",
        "secondshift/agent/image/agent.py",
        "secondshift/agent/image/claude/__init__.py",
        "secondshift/agent/image/claude/agents.json",
        "secondshift/agent/image/claude/command.py",
        "secondshift/agent/image/claude/events.py",
        "secondshift/agent/image/claude/mcp.json",
        "secondshift/agent/image/claude/settings.json",
        "secondshift/agent/image/claude/subagent_hook.py",
        "secondshift/agent/image/claude/worker_prompt.md",
        "secondshift/agent/image/codex/__init__.py",
        "secondshift/agent/image/codex/broker.toml",
        "secondshift/agent/image/codex/command.py",
        "secondshift/agent/image/codex/config.toml",
        "secondshift/agent/image/codex/events.py",
        "secondshift/agent/image/egress.py",
        "secondshift/agent/image/outcomes.py",
        "secondshift/agent/image/package-lock.json",
        "secondshift/agent/image/package.json",
    ]
    assert seen["build"][-3:-1] == ["-t", "secondshift-agent:local"]


def test_the_agent_image_copies_the_whole_folder() -> None:
    dockerfile = (AGENT_IMAGE_DIR / "Dockerfile").read_text()

    assert "COPY secondshift/agent/image/ " in dockerfile


def test_the_cli_releases_are_locked() -> None:
    """npm ci installs exactly the lock, so it must match package.json."""
    wanted = json.loads((AGENT_IMAGE_DIR / "package.json").read_text())
    lock = json.loads((AGENT_IMAGE_DIR / "package-lock.json").read_text())
    dependencies = wanted["dependencies"]

    assert lock["packages"][""]["dependencies"] == dependencies
    for name, version in dependencies.items():
        assert lock["packages"][f"node_modules/{name}"]["version"] == version
    assert all(
        package.get("integrity", "").startswith("sha512-")
        for path, package in lock["packages"].items()
        if path
    )
    assert "npm ci" in (AGENT_IMAGE_DIR / "Dockerfile").read_text()


def test_the_command_prints_the_image_id_for_the_tag_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tags: list[str] = []

    def build(tag: str) -> str:
        tags.append(tag)
        return "sha256:" + "c" * 64

    monkeypatch.setattr(agent_build, "build_agent_image", build)

    agent_build.main([])
    agent_build.main(["--tag", "secondshift-agent:production"])

    assert tags == ["secondshift-agent:dev", "secondshift-agent:production"]
    assert capsys.readouterr().out.split() == ["sha256:" + "c" * 64] * 2
