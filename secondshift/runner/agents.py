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

from secondshift.config.constants import HOST_ALIAS, AgentOutcome, SandboxRuntime
from secondshift.runner.agent_credentials import decrypt_agent_auth
from secondshift.runner.agent_protocol import AgentCheckInput, AgentInput, AgentOutput
from secondshift.runner.images import resolve_image_id
from secondshift.runner.models import RunnerSession
from secondshift.runner.protocol import CommandRequest, RunnerProtocolError
from secondshift.runner.services import DockerRunnerService


def agent_identity() -> tuple[int, int]:
    return (settings.RUNNER_SANDBOX_UID or 10001, settings.RUNNER_SANDBOX_GID or 10001)


def agent_name(session_id: str) -> str:
    return f"secondshift-agent-{session_id}"


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
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            raise RunnerProtocolError(
                "The agent image isn't built. Restart SecondShift to build it."
            ) from error
    DockerRunnerService().verify_runtime()
    return image


def agent_command(session_id: str, auth_path: Path, image: str) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--name",
        agent_name(session_id),
    ]
    if settings.RUNNER_RUNTIME != SandboxRuntime.DOCKER:
        command += ["--runtime", settings.RUNNER_RUNTIME]
    if settings.AGENT_RESOLV_CONF:
        resolver = Path(settings.AGENT_RESOLV_CONF)
        if (
            not resolver.is_absolute()
            or not resolver.is_file()
            or resolver.is_symlink()
        ):
            raise RunnerProtocolError(
                "The agent resolver must be an existing absolute file."
            )
        command += [f"--mount=type=bind,src={resolver},dst=/etc/resolv.conf,readonly"]
    uid, gid = agent_identity()
    return [
        *command,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        f"--pids-limit={settings.RUNNER_PIDS_LIMIT}",
        f"--memory={settings.RUNNER_MEMORY}",
        f"--memory-swap={settings.RUNNER_MEMORY}",
        f"--cpus={settings.RUNNER_CPUS}",
        f"--network={settings.AGENT_NETWORK}",
        # Docker Desktop defines this alias. Native Linux Docker needs it added.
        f"--add-host={HOST_ALIAS}:host-gateway",
        f"--user={uid}:{gid}",
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
        "secondshift.runner.agent_image.agent",
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
    name = "auth.json" if request.backend == "codex" else "claude-token"
    target = path / name
    target.write_text(auth)
    target.chmod(0o600)
    uid, gid = agent_identity()
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
    if not RunnerSession.objects.filter(
        session_id=session_id, status=RunnerSession.Status.ACTIVE
    ).exists():
        raise RunnerProtocolError("Unknown or inactive runner session.")
    # Reserve once, before starting the process: an ambiguous outcome is not retried.
    reservation = CommandRequest(
        call_id="agent-execution", argv=(request.backend, request.credential_key)
    )
    service = DockerRunnerService()
    if service.reserve_command(session_id, reservation) is not None:
        raise RunnerProtocolError("This agent execution cannot be repeated.")
    auth_path = provision_auth(request)
    configuration = {
        "backend": request.backend,
        "broker_url": settings.AGENT_BROKER_URL,
        "broker_token": request.broker_token.get_secret_value(),
        "prompt": request.prompt,
        "max_turns": request.max_turns,
        "token_budget": request.token_budget,
    }
    return run_agent_container(
        agent_name(session_id),
        agent_command(session_id, auth_path, image),
        configuration,
        request.timeout_seconds,
    )


def check_agent(request: AgentCheckInput) -> AgentOutput:
    """Run the login's CLI once with a fixed prompt, no tools and no repository."""
    with credential_lock(request.credential_key):
        image = require_agent_configuration()
        auth_path = provision_auth(request)
        name = f"check-{request.credential_key}"
        configuration = {
            "backend": request.backend,
            "prompt": CHECK_PROMPT,
            "max_turns": 1,
            "token_budget": CHECK_TOKEN_BUDGET,
        }
        return run_agent_container(
            agent_name(name),
            agent_command(name, auth_path, image),
            configuration,
            CHECK_TIMEOUT_SECONDS,
        )


def run_agent_container(
    name: str, command: list[str], configuration: dict[str, object], timeout: int
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
            if (
                len(data) > settings.RUNNER_COMMAND_OUTPUT_MAX_BYTES
                or completed.returncode
            ):
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
