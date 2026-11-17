"""Setup commands: a job's dependency installs, before the agent starts.

Each command runs in its own short-lived container, with the session's image
and every sandbox flag. It sees only the workspace: not the files earlier
runs saved, not the outputs folder, and not a skill from another repository.
It joins an internal network whose only way out is a proxy that allows
package registries, never the agents' providers. The agent's sandbox stays
offline throughout.

Setup holds only the repository at the run's commit, which whoever can merge
already controls. No trigger payload, tool result or model output has entered
the session yet, and the session's first agent call closes setup for good.
"""

import hashlib
import ipaddress
from contextlib import suppress
from pathlib import Path

from django.conf import settings

from secondshift.agent.image.egress import PROXY_PORT
from secondshift.docker.commands import DockerError, resolve_image_id, run
from secondshift.runner.collection import BASELINE_DIR, record_setup_baseline
from secondshift.runner.dao import complete_command, get_session
from secondshift.runner.egress import (
    ensure_local_proxy,
    proxy_lock,
    require_internal_network,
)
from secondshift.runner.models import RunnerSession
from secondshift.runner.services import (
    DockerRunnerService,
    sandbox_flags,
    setup_name,
)
from secondshift.runner_protocol.sandbox import SANDBOX_WORKSPACE_DIR
from secondshift.runner_protocol.sessions import (
    CommandRequest,
    CommandResult,
    RunnerProtocolError,
    setup_call_id,
)

LOCAL_SETUP_PROXY_PREFIX = "secondshift-setup-proxy"
SETUP_OFF = (
    "This server doesn't run setup commands. Its admin turns them on by "
    "running the setup proxy and setting SECONDSHIFT_RUNNER_SETUP_PROXY_ADDRESS."
)


def local_setup_proxy_name() -> str:
    """One per local instance, so two on one machine keep their own."""
    root = str(settings.RUNNER_WORK_ROOT.resolve())
    return (
        f"{LOCAL_SETUP_PROXY_PREFIX}-{hashlib.sha256(root.encode()).hexdigest()[:12]}"
    )


def local_setup_proxy_command(image: str) -> list[str]:
    """The local setup proxy's docker create arguments. All server-owned."""
    return [
        *sandbox_flags(),
        f"--network={settings.RUNNER_SETUP_NETWORK}",
        "--entrypoint=python3",
        image,
        "-m",
        "secondshift.agent.image.egress",
        "--allow-registries",
    ]


def proxy_image() -> str:
    """The agent image, which holds the proxy. One machine builds it as a tag."""
    if not settings.AGENT_IMAGE:
        raise RunnerProtocolError(SETUP_OFF)
    try:
        return resolve_image_id(settings.AGENT_IMAGE)
    except DockerError as error:
        raise RunnerProtocolError(
            "The agent image isn't built, and the setup proxy runs from it. "
            "Restart SecondShift to build it."
        ) from error


def setup_proxy_address() -> str:
    """Check the setup network and find its proxy. Starts it on one machine."""
    network = settings.RUNNER_SETUP_NETWORK
    if settings.RUNNER_SETUP_PROXY_ADDRESS:
        require_internal_network(network, create=False)
        try:
            return str(ipaddress.ip_address(settings.RUNNER_SETUP_PROXY_ADDRESS))
        except ValueError:
            raise RunnerProtocolError("The setup proxy address is invalid.") from None
    if not settings.RUNNER_SETUP_MANAGED_PROXY:
        raise RunnerProtocolError(SETUP_OFF)
    command = local_setup_proxy_command(proxy_image())
    with proxy_lock():
        require_internal_network(network, create=True)
        return ensure_local_proxy(local_setup_proxy_name(), command, network)


def stop_local_setup_proxy() -> None:
    """Remove this instance's setup proxy when its runner stops."""
    if not settings.RUNNER_SETUP_MANAGED_PROXY:
        return
    with suppress(DockerError):
        run("rm", "--force", local_setup_proxy_name())


def setup_command(
    session: RunnerSession, proxy_address: str, command: str
) -> list[str]:
    """The docker run arguments for one setup command.

    Only the shell command comes from the repository. It is one argument
    after the image, so it can't add a Docker flag.
    """
    workspace = Path(session.workspace_path)
    proxy = f"http://{proxy_address}:{PROXY_PORT}"
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        setup_name(session.session_id),
        *sandbox_flags(),
        # Internal, so the registry proxy is the only way out.
        f"--network={settings.RUNNER_SETUP_NETWORK}",
        # Package caches live here and go with the container.
        "--tmpfs=/tmp:rw,nosuid,size=1g",
        # Only the workspace. Installs that should last go in it, such as
        # node_modules or .venv.
        f"--mount=type=bind,src={workspace},dst={SANDBOX_WORKSPACE_DIR}",
        f"--workdir={SANDBOX_WORKSPACE_DIR}",
        "--env=HOME=/tmp",
        f"--env=HTTPS_PROXY={proxy}",
        f"--env=https_proxy={proxy}",
        "--env=GIT_CONFIG_COUNT=1",
        "--env=GIT_CONFIG_KEY_0=safe.directory",
        f"--env=GIT_CONFIG_VALUE_0={SANDBOX_WORKSPACE_DIR}",
        session.image,
        "sh",
        "-c",
        command,
    ]


def setup_request(command: str, index: int) -> CommandRequest:
    return CommandRequest(
        call_id=setup_call_id(index),
        argv=("sh", "-c", command),
        timeout_seconds=settings.RUNNER_SETUP_TIMEOUT_SECONDS,
    )


def run_setup(session_id: str, index: int) -> CommandResult:
    """Run the session's setup command at this index, once.

    A repeated call returns the first result. After the last command
    succeeds, the baseline takes in what setup changed.
    """
    service = DockerRunnerService()
    try:
        session = get_session(session_id)
    except RunnerSession.DoesNotExist as error:
        raise RunnerProtocolError("Unknown runner session.") from error
    if not 0 <= index < len(session.setup):
        raise RunnerProtocolError("The session has no setup command at that index.")
    request = setup_request(session.setup[index], index)
    # Before the reservation, so a missing proxy leaves the run retryable.
    proxy_address = setup_proxy_address()
    result = service.reserve_command(session_id, request, setup=True)
    if result is None:
        name = setup_name(session_id)
        service.remove_container(name)
        result = service.run_captured(
            request.call_id,
            setup_command(session, proxy_address, session.setup[index]),
            request.timeout_seconds,
        )
        if result.timed_out:
            service.remove_container(name)
        complete_command(
            session, request.call_id, result.exit_code, result.output, result.timed_out
        )
    workspace = Path(session.workspace_path)
    last = index == len(session.setup) - 1
    if last and result.exit_code == 0 and (workspace.parent / BASELINE_DIR).is_dir():
        record_setup_baseline(workspace.parent, workspace)
    return result
