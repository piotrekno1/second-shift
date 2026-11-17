"""Agent subscriptions for `secondshift up`.

Agent containers call the model gateway on the host through Docker's host
alias. Docker Desktop forwards that alias to the host's loopback, so the
gateway stays private. Native Linux Docker reaches the host on the default
bridge's gateway address instead, which other machines cannot route to.
"""

import ipaddress
import sys
from collections.abc import Callable

from secondshift.agent.build import build_agent_image
from secondshift.config.constants import LOCAL_AGENT_TAG
from secondshift.docker import commands
from secondshift.docker.commands import DockerError, resolve_image_id


class AgentSetupError(Exception):
    pass


def prepare_agent_image(image: str, echo: Callable[[str], None]) -> None:
    """Build the local tag the runner resolves. A chosen image is left alone."""
    if image != LOCAL_AGENT_TAG:
        return
    echo("Preparing the agent image. The first start takes a few minutes.")
    try:
        build_agent_image()
    except DockerError as error:
        if existing_image():
            echo("Couldn't update the agent image, so using the last one built.")
            return
        raise AgentSetupError(
            f"Couldn't prepare the agent image: {error.reason}"
        ) from error


def existing_image() -> str | None:
    try:
        return resolve_image_id(LOCAL_AGENT_TAG)
    except DockerError:
        return None


def with_container_binds(binds: list[str]) -> list[str]:
    """Add the bridge address on native Linux Docker, so the egress proxy can
    reach the gateway. Docker Desktop reaches loopback, so binds stay as they are.
    """
    host = container_gateway_host()
    if not host or not binds:
        return binds
    port = binds[0].rpartition(":")[2]
    return [*binds, f"{host}:{port}"]


def container_gateway_host() -> str | None:
    """Where the gateway must also listen for containers, if not loopback."""
    if sys.platform != "linux" or commands.is_docker_desktop():
        return None
    try:
        gateways = commands.output(
            "network",
            "inspect",
            "bridge",
            "--format",
            "{{range .IPAM.Config}}{{.Gateway}} {{end}}",
            timeout=commands.INFO_TIMEOUT_SECONDS,
        )
    except DockerError as error:
        raise AgentSetupError("Couldn't find Docker's bridge address.") from error
    for value in gateways.split():
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and address.is_private and not address.is_unspecified:
            return str(address)
    raise AgentSetupError("Couldn't find Docker's bridge address.")
