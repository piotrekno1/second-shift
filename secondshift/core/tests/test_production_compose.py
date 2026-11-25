"""Keep production service networks and artifact access separated."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from secondshift.runner_protocol.agents import AGENT_BROKER_PATH

ROOT = Path(__file__).resolve().parents[3]


def production_services() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "compose.production.yaml").read_text())["services"]


def compose_file(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / name).read_text())


def test_nothing_of_the_control_plane_joins_the_agent_network() -> None:
    services = production_services()
    for role, service in services.items():
        if role != "agent-proxy":
            assert "agents" not in service.get("networks", {})
    for role in ("web", "worker", "gateway", "runner"):
        assert "SECONDSHIFT_AGENT_IMAGE" in services[role]["environment"]
        assert (
            "AGENT_PROXY_ADDRESS"
            in services[role]["environment"]["SECONDSHIFT_AGENT_BROKER_URL"]
        )


def test_agents_leave_only_through_the_egress_proxy() -> None:
    compose = compose_file("compose.production.yaml")
    services = compose["services"]
    assert compose["networks"]["agents"]["internal"] is True
    proxy = services["agent-proxy"]
    assert set(proxy["networks"]) == {"agents", "relay", "egress"}
    assert "environment" not in proxy and "env_file" not in proxy
    assert "volumes" not in proxy
    for role, service in services.items():
        if role != "agent-proxy":
            assert "egress" not in service.get("networks", {})
    address = proxy["networks"]["agents"]["ipv4_address"]
    assert services["runner"]["environment"]["SECONDSHIFT_AGENT_PROXY_ADDRESS"] == (
        address
    )


@pytest.mark.parametrize("name", ["compose.production.yaml", "compose.runner.yaml"])
def test_agents_reach_tools_only_through_their_runners_relay(name: str) -> None:
    compose = compose_file(name)
    services = compose["services"]
    assert compose["networks"]["relay"]["internal"] is True
    joined = {
        role
        for role, service in services.items()
        if "relay" in service.get("networks", {})
    }
    assert joined == {"runner", "agent-proxy"}
    runner = services["runner"]["environment"]
    relay_port = runner["SECONDSHIFT_RUNNER_AGENT_RELAY_PORT"]
    forward = services["agent-proxy"]["command"]
    listen = forward[forward.index("--forward") + 1]
    assert listen == f"${{RELAY_FORWARD_PORT:-8001}}:runner:{relay_port}"
    # The broker URL is the proxy's forward, on every runner.
    assert runner["SECONDSHIFT_AGENT_BROKER_URL"] == (
        "http://${AGENT_PROXY_ADDRESS:-172.30.0.3}:${RELAY_FORWARD_PORT:-8001}/"
        f"{AGENT_BROKER_PATH}"
    )


def test_setup_leaves_only_through_a_registry_proxy_and_is_off_by_default() -> None:
    compose = yaml.safe_load((ROOT / "compose.production.yaml").read_text())
    services = compose["services"]
    assert compose["networks"]["setup"]["internal"] is True
    proxy = services["setup-proxy"]
    assert proxy["profiles"] == ["setup"]
    assert proxy["command"][-1] == "--allow-registries"
    assert set(proxy["networks"]) == {"setup", "setup-egress"}
    assert "environment" not in proxy and "volumes" not in proxy
    for role, service in services.items():
        if role != "setup-proxy":
            assert "setup" not in service.get("networks", {})
            assert "setup-egress" not in service.get("networks", {})
    runner = services["runner"]["environment"]
    assert (
        runner["SECONDSHIFT_RUNNER_SETUP_PROXY_ADDRESS"] == "${SETUP_PROXY_ADDRESS:-}"
    )


def test_gateway_artifacts_are_read_only_and_runner_has_no_store() -> None:
    services = production_services()
    assert (
        "artifacts:/var/lib/secondshift/artifacts:ro" in services["gateway"]["volumes"]
    )
    for role in ("web", "worker"):
        assert "artifacts:/var/lib/secondshift/artifacts" in services[role]["volumes"]
    assert not any("ARTIFACT" in key for key in services["runner"]["environment"])
    assert not any("artifacts" in str(mount) for mount in services["runner"]["volumes"])
    assert (
        "ARTIFACT_READ_ACCESS_KEY_ID"
        in services["gateway"]["environment"]["SECONDSHIFT_ARTIFACT_ACCESS_KEY_ID"]
    )
    assert (
        "ARTIFACT_READ_SECRET_ACCESS_KEY"
        in services["gateway"]["environment"]["SECONDSHIFT_ARTIFACT_SECRET_ACCESS_KEY"]
    )
