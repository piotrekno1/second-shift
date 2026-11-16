from pathlib import Path
from unittest.mock import Mock

import pytest

from secondshift.agent.build import (
    AGENT_IMAGE_DIR,
    build_agent_image,
    resolve_image_id,
)


def test_resolves_mutable_tag_to_immutable_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id = "sha256:" + "a" * 64
    run = Mock(return_value=Mock(stdout=image_id + "\n"))
    monkeypatch.setattr("secondshift.agent.build.subprocess.run", run)
    assert resolve_image_id("secondshift-agent:dev") == image_id
    assert run.call_args.args[0] == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{.Id}}",
        "secondshift-agent:dev",
    ]


def test_rejects_unexpected_inspection_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "secondshift.agent.build.subprocess.run",
        Mock(return_value=Mock(stdout="latest")),
    )
    with pytest.raises(ValueError, match="immutable"):
        resolve_image_id("secondshift-agent:dev")


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
        return Mock(stdout=image_id)

    monkeypatch.setattr("secondshift.agent.build.subprocess.run", run)

    assert build_agent_image() == image_id
    assert seen["files"] == [
        "secondshift/agent/image/Dockerfile",
        "secondshift/agent/image/agent.py",
        "secondshift/agent/image/claude.py",
        "secondshift/agent/image/codex.py",
        "secondshift/agent/image/outcomes.py",
        "secondshift/agent/image/subagent_hook.py",
    ]
    assert seen["build"][-3:-1] == ["-t", "secondshift-agent:local"]


def test_the_agent_image_copies_every_module() -> None:
    dockerfile = (AGENT_IMAGE_DIR / "Dockerfile").read_text()

    assert "COPY secondshift/agent/image/*.py " in dockerfile
