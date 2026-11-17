"""Server-owned agent containers. Repository command sessions stay offline."""

import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

from secondshift.agent.image.outcomes import (
    AUTH_FILES,
    AgentConfig,
    AgentOutcome,
)
from secondshift.docker.commands import DockerError, resolve_image_id
from secondshift.runner.agent_credentials import decrypt_agent_auth
from secondshift.runner.dao import is_session_active
from secondshift.runner.egress import AgentEgress, prepare_egress
from secondshift.runner.services import DockerRunnerService, agent_name, sandbox_flags
from secondshift.runner_protocol.schemas import AgentCheckInput, AgentInput, AgentOutput
from secondshift.runner_protocol.sessions import CommandRequest, RunnerProtocolError

# A login check asks for one short answer, with no tools.
CHECK_PROMPT = "Reply with the single word OK."
CHECK_TOKEN_BUDGET = 50_000
CHECK_TIMEOUT_SECONDS = 20


def require_agent_configuration() -> str:
    """The exact image to run. Only a local runner may resolve a tag."""
    if not settings.AGENT_IMAGE or not settings.AGENT_BROKER_URL:
        raise RunnerProtocolError(
            "CLI agent execution is not configured on the runner."
        )
    image = settings.AGENT_IMAGE
    if not re.fullmatch(r"(?:[^@\s]+@)?sha256:[0-9a-f]{64}", image):
        if not settings.AGENT_IMAGE_TAGS_ALLOWED:
            raise RunnerProtocolError("The agent image must be pinned by digest.")
        try:
            image = resolve_image_id(image)
        except DockerError as error:
            raise RunnerProtocolError(
                "The agent image isn't built. Restart SecondShift to build it."
            ) from error
    DockerRunnerService().verify_runtime()
    return image


def agent_command(
    session_id: str, auth_path: Path, image: str, egress: AgentEgress
) -> list[str]:
    uid, gid = settings.RUNNER_SANDBOX_UID, settings.RUNNER_SANDBOX_GID
    # No resolver. Agents reach the proxy and the gateway by address, and the
    # proxy looks up provider names itself.
    return [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--name",
        agent_name(session_id),
        *sandbox_flags(),
        # Internal, so the egress proxy is the only way out.
        f"--network={settings.AGENT_NETWORK}",
        *egress.flags(),
        f"--tmpfs=/tmp:rw,nosuid,size=256m,uid={uid},gid={gid}",
        f"--mount=type=bind,src={auth_path},dst=/auth",
        "--workdir=/tmp",
        "--env=HOME=/tmp",
        "--env=CODEX_HOME=/auth",
        "--env=CLAUDE_CONFIG_DIR=/tmp/claude",
        "--env=DISABLE_AUTOUPDATER=1",
        f"--label=secondshift.agent-credential={auth_path.name}",
        "--log-driver=none",
        "--entrypoint=python3",
        image,
        "-m",
        "secondshift.agent.image.agent",
    ]


def provision_auth(request: AgentInput | AgentCheckInput) -> Path:
    root = settings.AGENT_AUTH_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / request.credential_key
    # Keys are random, server-issued, immutable per reconnect. No paths from repos.
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise RunnerProtocolError("Invalid agent credential directory.")
        return path
    auth = decrypt_agent_auth(request.auth.get_secret_value())
    path.mkdir(mode=0o700)
    target = path / AUTH_FILES[request.backend]
    target.write_text(auth)
    target.chmod(0o600)
    uid, gid = settings.RUNNER_SANDBOX_UID, settings.RUNNER_SANDBOX_GID
    if os.geteuid() == 0:
        os.chown(target, uid, gid)
        os.chown(path, uid, gid)
    return path


@contextmanager
def credential_lock(key: str) -> Iterator[None]:
    root = settings.AGENT_AUTH_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / f"{key}.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerProtocolError(
                "This agent login is already running another job."
            ) from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def execute_agent(session_id: str, request: AgentInput) -> AgentOutput:
    with credential_lock(request.credential_key):
        return execute_locked_agent(session_id, request)


def execute_locked_agent(session_id: str, request: AgentInput) -> AgentOutput:
    image = require_agent_configuration()
    if not is_session_active(session_id):
        raise RunnerProtocolError("Unknown or inactive runner session.")
    # Before the reservation, so a missing proxy leaves the run retryable.
    egress = prepare_egress(image)
    # Reserve once, before starting the process: an ambiguous outcome is not retried.
    reservation = CommandRequest(
        call_id="agent-execution", argv=(request.backend, request.credential_key)
    )
    service = DockerRunnerService()
    if service.reserve_command(session_id, reservation) is not None:
        raise RunnerProtocolError("This agent execution cannot be repeated.")
    auth_path = provision_auth(request)
    configuration: AgentConfig = {
        "backend": request.backend,
        "broker_url": settings.AGENT_BROKER_URL,
        "broker_token": request.broker_token.get_secret_value(),
        "prompt": request.prompt,
        "max_turns": request.max_turns,
        "token_budget": request.token_budget,
        "subagents": request.subagents if request.backend.starts_subagents else 0,
    }
    return run_agent_container(
        agent_name(session_id),
        agent_command(session_id, auth_path, image, egress),
        configuration,
        request.timeout_seconds,
    )


def check_agent(request: AgentCheckInput) -> AgentOutput:
    """Run the login's CLI once with a fixed prompt, no tools and no repository."""
    with credential_lock(request.credential_key):
        image = require_agent_configuration()
        auth_path = provision_auth(request)
        name = f"check-{request.credential_key}"
        configuration: AgentConfig = {
            "backend": request.backend,
            "prompt": CHECK_PROMPT,
            "max_turns": 1,
            "token_budget": CHECK_TOKEN_BUDGET,
        }
        return run_agent_container(
            agent_name(name),
            agent_command(name, auth_path, image, prepare_egress(image)),
            configuration,
            CHECK_TIMEOUT_SECONDS,
        )


def run_agent_container(
    name: str, command: list[str], configuration: AgentConfig, timeout: int
) -> AgentOutput:
    # Auth and the broker token never appear in argv, Docker env, logs, or DBOS.
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(configuration).encode(),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            output.seek(0)
            data = output.read(settings.RUNNER_COMMAND_OUTPUT_MAX_BYTES + 1)
            if completed.returncode:
                return AgentOutput(reason=AgentOutcome.AGENT_STOPPED)
            if len(data) > settings.RUNNER_COMMAND_OUTPUT_MAX_BYTES:
                return AgentOutput(reason=AgentOutcome.AGENT_FAILED)
            try:
                return AgentOutput.model_validate_json(data)
            except ValueError:
                return AgentOutput(reason=AgentOutcome.INVALID_AGENT_OUTPUT)
        except subprocess.TimeoutExpired:
            return AgentOutput(reason=AgentOutcome.TIMED_OUT)
        finally:
            DockerRunnerService().remove_container(name)


def remove_agent_credentials(key: str) -> None:
    """Stop consumers before removing a disconnected login's CLI cache."""
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise RunnerProtocolError("Invalid credential key.")
    service = DockerRunnerService()
    listed = service.run_command(
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=secondshift.agent-credential={key}",
        ],
        timeout=15,
    )
    if listed.returncode:
        raise RunnerProtocolError("The runner could not revoke agent credentials.")
    for container in listed.stdout.splitlines():
        if not re.fullmatch(r"[0-9a-f]{12,64}", container):
            raise RunnerProtocolError(
                "Docker returned an invalid container identifier."
            )
        service.remove_container(container)
    with credential_lock(key):
        path = settings.AGENT_AUTH_ROOT.resolve() / key
        if path.is_symlink():
            raise RunnerProtocolError("Invalid agent credential directory.")
        if path.exists():
            shutil.rmtree(path)
