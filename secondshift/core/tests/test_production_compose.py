"""Keep production service networks and artifact access separated."""

from pathlib import Path
from typing import Any

import yaml

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
