import subprocess
from unittest.mock import Mock

import pytest

from secondshift.core import local_agents

IMAGE_ID = "sha256:" + "c" * 64


def test_up_builds_the_agent_image_and_points_agents_at_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_agents, "build_agent_image", Mock(return_value=IMAGE_ID))
    messages: list[str] = []

    env = local_agents.agent_env(
        {}, "http://host.docker.internal:8001/gateway/agent/mcp", messages.append
    )

    assert env == {
        "SECONDSHIFT_AGENT_IMAGE": IMAGE_ID,
        "SECONDSHIFT_AGENT_BROKER_URL": (
            "http://host.docker.internal:8001/gateway/agent/mcp"
        ),
    }
    assert "first start" in messages[0]


def test_up_uses_an_image_the_person_chose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build = Mock()
    monkeypatch.setattr(local_agents, "build_agent_image", build)
    monkeypatch.setattr(local_agents, "resolve_image_id", Mock(return_value=IMAGE_ID))
    pinned = "registry.example/agent@sha256:" + "d" * 64

    chosen = local_agents.agent_env(
        {"SECONDSHIFT_AGENT_IMAGE": pinned}, "http://broker", print
    )
    tagged = local_agents.agent_env(
        {"SECONDSHIFT_AGENT_IMAGE": "my-agent:latest"}, "http://broker", print
    )

    assert chosen["SECONDSHIFT_AGENT_IMAGE"] == pinned
    assert tagged["SECONDSHIFT_AGENT_IMAGE"] == IMAGE_ID
    build.assert_not_called()


def test_failed_build_uses_the_last_image_or_explains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = subprocess.CalledProcessError(
        1, ["docker", "build"], stderr="step 1\nnetwork unreachable\n"
    )
    monkeypatch.setattr(local_agents, "build_agent_image", Mock(side_effect=failure))
    monkeypatch.setattr(local_agents, "resolve_image_id", Mock(return_value=IMAGE_ID))
    messages: list[str] = []

    assert local_agents.agent_image("", messages.append) == IMAGE_ID
    assert "last one built" in messages[-1]

    monkeypatch.setattr(local_agents, "resolve_image_id", Mock(side_effect=failure))
    with pytest.raises(local_agents.AgentSetupError, match="network unreachable"):
        local_agents.agent_image("", messages.append)


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
            return Mock(stdout=operating_system + "\n")
        return Mock(stdout="0.0.0.0 172.17.0.1 fd00::1 ")

    monkeypatch.setattr(local_agents.sys, "platform", platform)
    monkeypatch.setattr(local_agents.subprocess, "run", run)

    assert local_agents.container_gateway_host() == expected
