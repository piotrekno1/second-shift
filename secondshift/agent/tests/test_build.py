from pathlib import Path
from unittest.mock import Mock

import pytest

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
    ]
    assert seen["build"][-3:-1] == ["-t", "secondshift-agent:local"]


def test_the_agent_image_copies_the_whole_folder() -> None:
    dockerfile = (AGENT_IMAGE_DIR / "Dockerfile").read_text()

    assert "COPY secondshift/agent/image/ " in dockerfile
