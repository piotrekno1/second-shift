from unittest.mock import Mock

import pytest

from secondshift.runner.dev import resolve_image_id


def test_dev_runner_resolves_mutable_tag_to_immutable_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_id = "sha256:" + "a" * 64
    run = Mock(return_value=Mock(stdout=image_id + "\n"))
    monkeypatch.setattr("secondshift.runner.dev.subprocess.run", run)
    assert resolve_image_id("secondshift-agent:dev") == image_id
    assert run.call_args.args[0] == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{.Id}}",
        "secondshift-agent:dev",
    ]


def test_dev_runner_rejects_unexpected_inspection_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "secondshift.runner.dev.subprocess.run",
        Mock(return_value=Mock(stdout="latest")),
    )
    with pytest.raises(ValueError, match="immutable"):
        resolve_image_id("secondshift-agent:dev")
