"""Keep production service networks and artifact access separated."""

from pathlib import Path
from typing import Any

import yaml

from secondshift.runner_protocol.agents import AGENT_BROKER_PATH

ROOT = Path(__file__).resolve().parents[3]


def production_services() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "compose.production.yaml").read_text())["services"]


def test_only_gateway_joins_the_agent_network() -> None:
    services = production_services()
    assert "agents" in services["gateway"]["networks"]
    for role in ("web", "worker", "runner", "postgres", "caddy"):
        assert "agents" not in services[role].get("networks", {})
    for role in ("web", "worker", "gateway", "runner"):
        assert "SECONDSHIFT_AGENT_IMAGE" in services[role]["environment"]
        assert (
            "AGENT_GATEWAY_ADDRESS"
            in services[role]["environment"]["SECONDSHIFT_AGENT_BROKER_URL"]
        )


def test_agents_leave_only_through_the_egress_proxy() -> None:
    compose = yaml.safe_load((ROOT / "compose.production.yaml").read_text())
    services = compose["services"]
    assert compose["networks"]["agents"]["internal"] is True
    proxy = services["agent-proxy"]
    assert set(proxy["networks"]) == {"agents", "egress"}
    assert "environment" not in proxy and "env_file" not in proxy
    assert "volumes" not in proxy
    for role, service in services.items():
        if role != "agent-proxy":
            assert "egress" not in service.get("networks", {})
    address = proxy["networks"]["agents"]["ipv4_address"]
    assert services["runner"]["environment"]["SECONDSHIFT_AGENT_PROXY_ADDRESS"] == (
        address
    )


def test_agents_reach_the_broker_at_the_gateways_route() -> None:
    services = production_services()
    for role in ("web", "worker", "gateway", "runner"):
        url = services[role]["environment"]["SECONDSHIFT_AGENT_BROKER_URL"]
        assert url.endswith(f":8001/{AGENT_BROKER_PATH}")


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
