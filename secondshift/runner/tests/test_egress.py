"""Agents leave only through the egress proxy, on an internal network."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from secondshift.runner import egress
from secondshift.runner.egress import AgentEgress, prepare_egress
from secondshift.runner_protocol.sessions import RunnerProtocolError

IMAGE = "sha256:" + "a" * 64
BROKER = "http://host.docker.internal:8001/gateway/agent/mcp"
PROXY = "secondshift-agent-proxy-8001"


class FakeDocker:
    """Answers docker commands from a table and records every call."""

    def __init__(self, answers: dict[str, tuple[int, str]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, ...]] = []

    def run(self, *arguments: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(arguments)
        for prefix, (code, out) in self.answers.items():
            if " ".join(arguments).startswith(prefix):
                return subprocess.CompletedProcess(arguments, code, out, "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def output(self, *arguments: str, **kwargs: Any) -> str:
        result = self.run(*arguments)
        if result.returncode:
            raise egress.DockerError("failed")
        return result.stdout.strip()

    def commands(self) -> list[str]:
        return [" ".join(call[:2]) for call in self.calls]


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(answers: dict[str, tuple[int, str]]) -> FakeDocker:
        fake = FakeDocker(answers)
        monkeypatch.setattr(egress, "run", fake.run)
        monkeypatch.setattr(egress, "output", fake.output)
        return fake

    return install


@pytest.fixture
def server(settings: Any) -> Any:
    settings.AGENT_NETWORK = "secondshift_agents"
    settings.AGENT_PROXY_ADDRESS = "172.30.0.3"
    settings.AGENT_MANAGED_PROXY = False
    settings.AGENT_BROKER_URL = "http://172.30.0.2:8001/gateway/agent/mcp"
    return settings


@pytest.fixture
def local(settings: Any, tmp_path: Path) -> Any:
    settings.AGENT_NETWORK = "secondshift-agents"
    settings.AGENT_PROXY_ADDRESS = ""
    settings.AGENT_MANAGED_PROXY = True
    settings.AGENT_BROKER_URL = BROKER
    settings.RUNNER_WORK_ROOT = tmp_path
    return settings


def networks(address: str) -> str:
    return json.dumps({"secondshift-agents": {"IPAddress": address}, "bridge": {}})


def test_a_server_uses_its_proxy_on_an_internal_network(
    server: Any, docker: Any
) -> None:
    fake = docker({"network inspect": (0, "true\n")})
    assert prepare_egress(IMAGE) == AgentEgress("172.30.0.3")
    assert fake.commands() == ["network inspect"]


@pytest.mark.parametrize(
    ("answer", "message"),
    [((0, "false\n"), "must be internal"), ((1, ""), "does not exist")],
)
def test_a_server_refuses_a_network_with_a_way_out(
    server: Any, docker: Any, answer: tuple[int, str], message: str
) -> None:
    fake = docker({"network inspect": answer})
    with pytest.raises(RunnerProtocolError, match=message):
        prepare_egress(IMAGE)
    # A server never creates or changes its network. Compose owns it.
    assert fake.commands() == ["network inspect"]


def test_a_server_without_a_proxy_fails_closed(server: Any, docker: Any) -> None:
    server.AGENT_PROXY_ADDRESS = ""
    fake = docker({})
    with pytest.raises(RunnerProtocolError, match="not configured"):
        prepare_egress(IMAGE)
    assert fake.calls == []


def test_one_machine_creates_an_internal_network_and_starts_the_proxy(
    local: Any, docker: Any
) -> None:
    fake = docker(
        {
            "network inspect": (1, ""),
            "container inspect --format {{.State": (1, ""),
            "container inspect --format {{json": (0, networks("172.24.0.2")),
        }
    )
    result = prepare_egress(IMAGE)

    assert result == AgentEgress("172.24.0.2", ("host.docker.internal",))
    assert ("network", "create", "--internal", "secondshift-agents") in fake.calls
    create = next(call for call in fake.calls if call[0] == "create")
    assert "--network=secondshift-agents" in create
    assert "--read-only" in create and "--cap-drop=ALL" in create
    assert create[-1] == "--forward=8001:host.docker.internal:8001"
    assert ("network", "connect", "bridge", PROXY) in fake.calls
    assert ("start", PROXY) in fake.calls


def test_a_running_proxy_with_the_same_settings_is_reused(
    local: Any, docker: Any
) -> None:
    command = egress.local_proxy_command(IMAGE, "host.docker.internal", 8001)
    config = egress.config_hash(command)
    fake = docker(
        {
            "network inspect": (0, "true\n"),
            "container inspect --format {{.State": (0, f"true {config}\n"),
            "container inspect --format {{json": (0, networks("172.24.0.2")),
        }
    )
    prepare_egress(IMAGE)
    assert "create" not in [call[0] for call in fake.calls]


def test_a_proxy_from_another_image_is_replaced(local: Any, docker: Any) -> None:
    fake = docker(
        {
            "network inspect": (0, "true\n"),
            "container inspect --format {{.State": (0, "true stale\n"),
            "container inspect --format {{json": (0, networks("172.24.0.2")),
        }
    )
    prepare_egress(IMAGE)
    assert ("rm", "--force", PROXY) in fake.calls
    assert "create" in [call[0] for call in fake.calls]


def test_one_machine_refuses_an_existing_network_with_a_way_out(
    local: Any, docker: Any
) -> None:
    local.AGENT_NETWORK = "bridge"
    fake = docker({"network inspect": (0, "false\n")})
    with pytest.raises(RunnerProtocolError, match="must be internal"):
        prepare_egress(IMAGE)
    assert "create" not in [call[0] for call in fake.calls]


def test_the_local_broker_must_be_a_name_the_proxy_can_stand_in_for(
    local: Any, docker: Any
) -> None:
    local.AGENT_BROKER_URL = "http://172.17.0.1:8001/gateway/agent/mcp"
    docker({"network inspect": (0, "true\n")})
    with pytest.raises(RunnerProtocolError, match="host name"):
        prepare_egress(IMAGE)


def test_agents_send_only_the_broker_around_the_proxy(local: Any) -> None:
    flags = AgentEgress("172.24.0.2", ("host.docker.internal",)).flags()
    assert "--add-host=host.docker.internal:172.24.0.2" in flags
    assert "--env=HTTPS_PROXY=http://172.24.0.2:3128" in flags
    assert "--env=NO_PROXY=host.docker.internal" in flags
    assert "--env=CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1" in flags


def test_a_stopping_runner_removes_only_its_own_proxy(local: Any, docker: Any) -> None:
    fake = docker({})
    egress.stop_local_proxy()
    assert fake.calls == [("rm", "--force", PROXY)]


def test_a_server_runner_leaves_the_compose_proxy_alone(
    server: Any, docker: Any
) -> None:
    fake = docker({})
    egress.stop_local_proxy()
    assert fake.calls == []
