"""Agent subscriptions for `secondshift up`.

Agent containers call the model gateway on the host through Docker's host
alias. Docker Desktop forwards that alias to the host's loopback, so the
gateway stays private. Native Linux Docker reaches the host on the default
bridge's gateway address instead, which other machines cannot route to.
"""

import ipaddress
import re
import subprocess
import sys
from collections.abc import Callable

from secondshift.config.constants import AppEnv
from secondshift.runner.images import (
    LOCAL_AGENT_TAG,
    build_agent_image,
    resolve_image_id,
)

AGENT_ENV_NAMES = (AppEnv.AGENT_IMAGE, AppEnv.AGENT_BROKER_URL)
PINNED_IMAGE = re.compile(r"(?:[^@\s]+@)?sha256:[0-9a-f]{64}")


class AgentSetupError(Exception):
    pass


def agent_env(
    parent: dict[str, str], broker_url: str, echo: Callable[[str], None]
) -> dict[str, str]:
    """The agent image, built unless one is set, and the configured broker."""
    return {
        AppEnv.AGENT_IMAGE: agent_image(parent.get(AppEnv.AGENT_IMAGE, ""), echo),
        # The runner doesn't load the control plane's settings, so pass it on.
        AppEnv.AGENT_BROKER_URL: broker_url,
    }


def agent_image(image: str, echo: Callable[[str], None]) -> str:
    if PINNED_IMAGE.fullmatch(image):
        return image
    try:
        if image:
            return resolve_image_id(image)
        echo("Preparing the agent image. The first start takes a few minutes.")
        return build_agent_image()
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        if not image and (existing := existing_image()):
            echo("Couldn't update the agent image, so using the last one built.")
            return existing
        raise AgentSetupError(
            f"Couldn't prepare the agent image: {failure_reason(error)}"
        ) from error


def existing_image() -> str | None:
    try:
        return resolve_image_id(LOCAL_AGENT_TAG)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def failure_reason(error: Exception) -> str:
    output = getattr(error, "stderr", None) or ""
    lines = [line for line in str(output).splitlines() if line.strip()]
    return lines[-1] if lines else str(error)


def container_gateway_host() -> str | None:
    """Where the gateway must also listen for containers, if not loopback."""
    if sys.platform != "linux" or docker_desktop():
        return None
    try:
        result = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                "bridge",
                "--format",
                "{{range .IPAM.Config}}{{.Gateway}} {{end}}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise AgentSetupError("Couldn't find Docker's bridge address.") from error
    for value in result.stdout.split():
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and address.is_private and not address.is_unspecified:
            return str(address)
    raise AgentSetupError("Couldn't find Docker's bridge address.")


def docker_desktop() -> bool:
    result = subprocess.run(
        ["docker", "info", "--format", "{{.OperatingSystem}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return result.stdout.strip() == "Docker Desktop"
