"""Server-owned agent containers. Repository command sessions stay offline."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
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
from secondshift.runner.relay import relaying
from secondshift.runner.services import DockerRunnerService, agent_name, sandbox_flags
from secondshift.runner_protocol.schemas import AgentCheckInput, AgentInput, AgentOutput
from secondshift.runner_protocol.sessions import CommandRequest, RunnerProtocolError

# A login check asks for one short answer, with no tools.
CHECK_PROMPT = "Reply with the single word OK."
CHECK_TOKEN_BUDGET = 50_000
CHECK_TIMEOUT_SECONDS = 20


def agents_configured() -> bool:
    """Whether this runner can run Claude Code and Codex."""
    return bool(settings.AGENT_IMAGE and settings.AGENT_BROKER_URL)


def require_agent_configuration() -> str:
    """The exact image to run. Only a local runner may resolve a tag."""
    if not agents_configured():
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
    """The login's directory, created on its first run or check.

    A login's runs share it, as terminals on a laptop share the CLI's home, so
    Codex's refreshed tokens reach every later run. It is built beside its
    final name and renamed into place, so runs starting together never see it
    half written.
    """
    root = settings.AGENT_AUTH_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / request.credential_key
    # Keys are random, server-issued, immutable per reconnect. No paths from repos.
    if not path.exists():
        auth = decrypt_agent_auth(request.auth.get_secret_value())
        staging = Path(tempfile.mkdtemp(dir=root, prefix=f".{request.credential_key}-"))
        target = staging / AUTH_FILES[request.backend]
        target.write_text(auth)
        target.chmod(0o600)
        uid, gid = settings.RUNNER_SANDBOX_UID, settings.RUNNER_SANDBOX_GID
        if os.geteuid() == 0:
            os.chown(target, uid, gid)
            os.chown(staging, uid, gid)
        try:
            staging.rename(path)
        except OSError:
            # Another run or check created it first.
            shutil.rmtree(staging)
    if path.is_symlink() or not path.is_dir():
        raise RunnerProtocolError("Invalid agent credential directory.")
    forget_cli_state(path)
    return path


def forget_cli_state(path: Path) -> None:
    """Keep only the login in its folder.

    Older releases ran Codex with this folder as its home, so its state,
    logs and memories from earlier runs may be here. Each run now has a home
    of its own.
    """
    logins = set(AUTH_FILES.values())
    for entry in path.iterdir():
        if entry.name in logins:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def execute_agent(session_id: str, request: AgentInput) -> AgentOutput:
    """Run the agent. Runs with the same login may run at once."""
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
    if request.model:
        configuration["model"] = request.model
    with relaying(session_id, request.broker_token.get_secret_value()):
        return run_agent_container(
            agent_name(session_id),
            agent_command(session_id, auth_path, image, egress),
            configuration,
            request.timeout_seconds,
        )


def check_agent(request: AgentCheckInput) -> AgentOutput:
    """Run the login's CLI once with a fixed prompt, no tools and no repository."""
    image = require_agent_configuration()
    auth_path = provision_auth(request)
    name = f"check-{request.credential_key}-{uuid.uuid4().hex[:8]}"
    configuration: AgentConfig = {
        "backend": request.backend,
        "prompt": CHECK_PROMPT,
        "max_turns": 1,
        "token_budget": CHECK_TOKEN_BUDGET,
    }
    if request.model:
        configuration["model"] = request.model
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
    root = settings.AGENT_AUTH_ROOT.resolve()
    path = root / key
    if path.is_symlink():
        raise RunnerProtocolError("Invalid agent credential directory.")
    if path.exists():
        shutil.rmtree(path)
    # Left by releases that let a login run one job at a time.
    (root / f"{key}.lock").unlink(missing_ok=True)
