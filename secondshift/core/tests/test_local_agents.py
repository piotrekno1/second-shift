from unittest.mock import Mock

import pytest

from secondshift.core import local_agents
from secondshift.docker import commands
from secondshift.docker.commands import DockerError

IMAGE_ID = "sha256:" + "c" * 64


@pytest.mark.parametrize(
    ("existing", "message"),
    [
        (None, "Preparing the agent image. The first start takes a few minutes."),
        (IMAGE_ID, "Checking the agent image."),
    ],
)
def test_up_builds_the_local_tag_but_leaves_a_chosen_image_alone(
    monkeypatch: pytest.MonkeyPatch, existing: str | None, message: str
) -> None:
    build = Mock(return_value=IMAGE_ID)
    monkeypatch.setattr(local_agents, "build_agent_image", build)
    monkeypatch.setattr(local_agents, "existing_image", Mock(return_value=existing))
    messages: list[str] = []

    local_agents.prepare_agent_image("secondshift-agent:local", messages.append)
    local_agents.prepare_agent_image("registry.example/agent@sha256:" + "d" * 64, print)
    local_agents.prepare_agent_image("", print)

    build.assert_called_once_with()
    assert messages == [message]


def test_failed_build_uses_the_last_image_or_explains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = DockerError("docker build failed.", "step 1\nnetwork unreachable\n")
    monkeypatch.setattr(local_agents, "build_agent_image", Mock(side_effect=failure))
    monkeypatch.setattr(local_agents, "resolve_image_id", Mock(return_value=IMAGE_ID))
    messages: list[str] = []

    local_agents.prepare_agent_image("secondshift-agent:local", messages.append)
    assert "last one built" in messages[-1]

    monkeypatch.setattr(local_agents, "resolve_image_id", Mock(side_effect=failure))
    with pytest.raises(local_agents.AgentSetupError, match="network unreachable"):
        local_agents.prepare_agent_image("secondshift-agent:local", messages.append)


@pytest.mark.parametrize(
    ("platform", "operating_system", "expected"),
    [
        ("darwin", "Docker Desktop", None),
        ("linux", "Docker Desktop", None),
        ("linux", "Ubuntu 24.04 LTS", "172.17.0.1"),
    ],
)
def test_gateway_opens_to_the_bridge_only_on_native_linux_docker(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    operating_system: str,
    expected: str | None,
) -> None:
    def run(argv: list[str], **kwargs: object) -> Mock:
        if argv[1] == "info":
            return Mock(returncode=0, stdout=operating_system + "\n")
        return Mock(returncode=0, stdout="0.0.0.0 172.17.0.1 fd00::1 ")

    monkeypatch.setattr(local_agents.sys, "platform", platform)
    monkeypatch.setattr(commands.subprocess, "run", run)

    assert local_agents.container_gateway_host() == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        (None, ["127.0.0.1:8001"]),
        ("172.17.0.1", ["127.0.0.1:8001", "172.17.0.1:8001"]),
    ],
)
def test_the_dev_gateway_adds_only_the_bridge_address(
    monkeypatch: pytest.MonkeyPatch, host: str | None, expected: list[str]
) -> None:
    monkeypatch.setattr(local_agents, "container_gateway_host", lambda: host)
    assert local_agents.with_container_binds(["127.0.0.1:8001"]) == expected
