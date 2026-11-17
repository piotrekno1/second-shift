"""How agent containers reach the internet: only through the egress proxy.

Agents join an internal Docker network with no route out. The proxy is the
one container on that network that can also reach the internet. A server runs
it as its own service. On one machine the runner starts it from the agent
image and points the gateway's host name at it.
"""

import fcntl
import hashlib
import ipaddress
import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings

from secondshift.agent.image.egress import PROXY_PORT
from secondshift.docker.commands import DockerError, output, run
from secondshift.runner.services import sandbox_flags
from secondshift.runner_protocol.agents import HOST_ALIAS
from secondshift.runner_protocol.sessions import RunnerProtocolError

# One proxy per local instance, named for its gateway port, so two instances
# on one machine never replace each other's proxy.
LOCAL_PROXY_PREFIX = "secondshift-agent-proxy"
# Docker's default network. The local proxy reaches the internet and the
# Docker host through it.
EGRESS_NETWORK = "bridge"
CONFIG_LABEL = "secondshift.agent-proxy"


@dataclass(frozen=True)
class AgentEgress:
    """Where an agent container sends its traffic."""

    proxy_address: str
    # Host names the agent resolves to the proxy, such as the local gateway.
    proxied_hosts: tuple[str, ...] = ()

    def flags(self) -> list[str]:
        proxy = f"http://{self.proxy_address}:{PROXY_PORT}"
        broker_host = urlsplit(settings.AGENT_BROKER_URL).hostname or ""
        return [
            *(f"--add-host={host}:{self.proxy_address}" for host in self.proxied_hosts),
            f"--env=HTTPS_PROXY={proxy}",
            f"--env=https_proxy={proxy}",
            # The broker is on the agent network itself, or forwarded by the proxy.
            f"--env=NO_PROXY={broker_host}",
            f"--env=no_proxy={broker_host}",
            # Claude Code's telemetry, error reports and update checks.
            "--env=CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
        ]


def require_internal_network(network: str, create: bool) -> None:
    """Fail closed unless the network has no route out."""
    try:
        found = run("network", "inspect", "--format", "{{.Internal}}", network)
        if found.returncode and create:
            output("network", "create", "--internal", network)
            return
    except DockerError as error:
        raise RunnerProtocolError(
            f"The runner could not check the network {network}."
        ) from error
    if found.returncode:
        raise RunnerProtocolError(f"The network {network} does not exist.")
    if found.stdout.strip() != "true":
        raise RunnerProtocolError(
            f"The network {network} must be internal, so containers on it reach "
            "the internet only through the egress proxy."
        )


def is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def prepare_egress(image: str) -> AgentEgress:
    """Check the network and find the proxy. Starts it on one machine."""
    if settings.AGENT_PROXY_ADDRESS:
        require_internal_network(settings.AGENT_NETWORK, create=False)
        try:
            address = ipaddress.ip_address(settings.AGENT_PROXY_ADDRESS)
        except ValueError:
            raise RunnerProtocolError("The agent proxy address is invalid.") from None
        return AgentEgress(str(address))
    if not settings.AGENT_MANAGED_PROXY:
        raise RunnerProtocolError("The agent egress proxy is not configured.")
    broker = urlsplit(settings.AGENT_BROKER_URL)
    if not broker.hostname or broker.port is None:
        raise RunnerProtocolError("The agent broker URL needs a host name and port.")
    if is_address(broker.hostname):
        # The proxy stands in for the gateway's name. An address can't be.
        raise RunnerProtocolError("The local agent broker URL must use a host name.")
    command = local_proxy_command(image, broker.hostname, broker.port)
    with proxy_lock():
        require_internal_network(settings.AGENT_NETWORK, create=True)
        address = ensure_local_proxy(
            local_proxy_name(broker.port), command, settings.AGENT_NETWORK
        )
    return AgentEgress(address, (broker.hostname,))


def local_proxy_command(image: str, gateway_host: str, gateway_port: int) -> list[str]:
    """The local proxy's docker create arguments. All of them are server-owned."""
    return [
        *sandbox_flags(),
        # The gateway listens on the Docker host. Native Linux Docker needs it added.
        f"--add-host={HOST_ALIAS}:host-gateway",
        f"--network={settings.AGENT_NETWORK}",
        "--entrypoint=python3",
        image,
        "-m",
        "secondshift.agent.image.egress",
        f"--forward={gateway_port}:{gateway_host}:{gateway_port}",
    ]


@contextmanager
def proxy_lock() -> Iterator[None]:
    """Only one run starts or replaces a local proxy at a time."""
    settings.RUNNER_WORK_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (settings.RUNNER_WORK_ROOT / "agent-proxy.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def config_hash(command: list[str]) -> str:
    """Labels the proxy, so a new image or gateway port replaces it."""
    return hashlib.sha256(json.dumps(command).encode()).hexdigest()


def local_proxy_name(gateway_port: int) -> str:
    return f"{LOCAL_PROXY_PREFIX}-{gateway_port}"


def stop_local_proxy() -> None:
    """Remove this instance's proxy when its runner stops. Servers have none."""
    port = urlsplit(settings.AGENT_BROKER_URL).port
    if not settings.AGENT_MANAGED_PROXY or port is None:
        return
    # If Docker is gone too, the next start replaces any proxy left behind.
    with suppress(DockerError):
        run("rm", "--force", local_proxy_name(port))


def ensure_local_proxy(name: str, command: list[str], network: str) -> str:
    """Start the proxy unless one with this exact configuration runs already.

    Returns its address on the internal network.
    """
    config = config_hash(command)
    try:
        state = run(
            "container",
            "inspect",
            "--format",
            f'{{{{.State.Running}}}} {{{{index .Config.Labels "{CONFIG_LABEL}"}}}}',
            name,
        )
        if state.returncode or state.stdout.split() != ["true", config]:
            run("rm", "--force", name)
            output(
                "create",
                "--name",
                name,
                f"--label={CONFIG_LABEL}={config}",
                *command,
            )
            output("network", "connect", EGRESS_NETWORK, name)
            output("start", name)
        networks = json.loads(
            output(
                "container",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                name,
            )
        )
        address = networks[network]["IPAddress"]
        return str(ipaddress.ip_address(address))
    except (DockerError, KeyError, TypeError, ValueError) as error:
        raise RunnerProtocolError(
            f"The runner could not start the egress proxy {name}."
        ) from error
