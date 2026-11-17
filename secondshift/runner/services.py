import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction

from secondshift.config.constants import SandboxRuntime
from secondshift.runner.collection import (
    collect,
    record_baseline,
    record_workspace_commit,
)
from secondshift.runner.dao import (
    complete_command,
    create_command,
    create_session,
    get_session,
    lock_session,
    session_command,
    set_command_timeout,
    set_session_status,
)
from secondshift.runner.models import RunnerCommand, RunnerSession
from secondshift.runner.sources import sweep_sources, unpack_source
from secondshift.runner_protocol.collection import OUTPUTS_DIR
from secondshift.runner_protocol.sandbox import (
    SANDBOX_HISTORY_DIR,
    SANDBOX_OUTPUTS_DIR,
    SANDBOX_SKILL_DIR,
)
from secondshift.runner_protocol.sessions import (
    SETUP_CALL_PREFIX,
    CommandRequest,
    CommandResult,
    RunnerProtocolError,
    RunnerSessionSpec,
)

TRUNCATION_MARKER = b"\n[... command output truncated by SecondShift ...]\n"
# Beside the workspace: the files earlier runs of the job saved.
HISTORY_DIR = "history"
# Beside the workspace: a skill from another repository.
SKILL_DIR = "skill"
# A command past its limit gets this long to stop after timeout's TERM
# before timeout sends KILL.
COMMAND_KILL_AFTER_SECONDS = 5
# How long past a command's limit the runner waits for timeout to end it.
# After that the runner stops the whole sandbox.
COMMAND_STOP_GRACE_SECONDS = 10


def sandbox_name(session_id: str) -> str:
    return f"secondshift-{session_id}"


def agent_name(session_id: str) -> str:
    return f"secondshift-agent-{session_id}"


def setup_name(session_id: str) -> str:
    return f"secondshift-setup-{session_id}"


def sandbox_flags() -> list[str]:
    """The runtime, hardening, limits and user every sandbox container gets.

    Callers add only their own mounts, network and temporary files.
    """
    flags = []
    if settings.RUNNER_RUNTIME != SandboxRuntime.DOCKER:
        flags += ["--runtime", settings.RUNNER_RUNTIME]
    return [
        *flags,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        f"--pids-limit={settings.RUNNER_PIDS_LIMIT}",
        f"--memory={settings.RUNNER_MEMORY}",
        # Docker otherwise lets the sandbox swap as much again as its memory.
        f"--memory-swap={settings.RUNNER_MEMORY}",
        f"--cpus={settings.RUNNER_CPUS}",
        f"--user={settings.RUNNER_SANDBOX_UID}:{settings.RUNNER_SANDBOX_GID}",
    ]


def enabled_images() -> list[str]:
    """The default image, then each named environment's. Nothing else runs."""
    return list(
        dict.fromkeys([settings.RUNNER_IMAGE, *settings.RUNNER_ENVIRONMENTS.values()])
    )


class RunnerConfigurationError(RuntimeError):
    pass


class DockerRunnerService:
    """Container operations and ORM-backed idempotency for the runner service."""

    def verify_configuration(self) -> None:
        settings.RUNNER_WORK_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        sweep_sources()
        self.verify_runtime()
        self.ensure_image()

    def ensure_image(self) -> None:
        """Pull every environment's image at startup, so no run waits on it."""
        for image in enabled_images():
            self.pull_image(image)

    def pull_image(self, image: str) -> None:
        if not self.run_command(
            ["docker", "image", "inspect", image], timeout=15
        ).returncode:
            return
        pulled = self.run_command(["docker", "pull", image], timeout=600)
        if pulled.returncode:
            detail = (pulled.stderr or pulled.stdout).strip().splitlines()
            reason = detail[-1] if detail else "no output"
            raise RunnerConfigurationError(
                f"Couldn't pull the sandbox image {image}: {reason}"
            )

    def verify_runtime(self) -> None:
        completed = self.run_command(
            ["docker", "info", "--format", "{{json .Runtimes}}"], timeout=15
        )
        if completed.returncode:
            raise RunnerConfigurationError("Docker is unavailable to the runner.")
        if settings.RUNNER_RUNTIME == SandboxRuntime.DOCKER:
            return
        try:
            runtimes = json.loads(completed.stdout)
        except (TypeError, ValueError) as error:
            raise RunnerConfigurationError(
                "Docker returned an invalid runtime list."
            ) from error
        if settings.RUNNER_RUNTIME not in runtimes:
            raise RunnerConfigurationError(
                f"Docker runtime {settings.RUNNER_RUNTIME} is not registered."
            )

    def start(self, spec: RunnerSessionSpec) -> str:
        if spec.image not in enabled_images():
            raise RunnerProtocolError(
                "The requested image is not enabled by this runner."
            )
        session_id = spec.session_id
        container_name = sandbox_name(session_id)
        session_root = (settings.RUNNER_WORK_ROOT / "sessions" / session_id).resolve()
        self.require_owned_path(session_root)
        workspace = session_root / "workspace"

        with transaction.atomic():
            session = lock_session(session_id)
            if session is None:
                try:
                    session = create_session(
                        session_id=session_id,
                        run_id=spec.run_id,
                        repository=spec.repository,
                        commit_sha=spec.commit_sha,
                        image=spec.image,
                        source_sha256=spec.source_sha256,
                        collect_changes=spec.collect_changes,
                        history_sha256=spec.history_sha256,
                        skill_sha256=spec.skill_sha256,
                        setup=list(spec.setup),
                        container_name=container_name,
                        workspace_path=str(workspace),
                    )
                except IntegrityError as error:
                    raise RunnerProtocolError(
                        "The run already has a runner session."
                    ) from error
            else:
                self.require_matching_spec(session, spec)
                if session.status == RunnerSession.Status.ACTIVE:
                    if not self.container_exists(container_name):
                        raise RunnerProtocolError(
                            "The recorded runner session has lost its container."
                        )
                    return session_id
                raise RunnerProtocolError(
                    "The previous session outcome is not reusable; it will not be repeated."
                )

        self.remove_container(container_name)
        self.remove_workspace(session_root)
        workspace.mkdir(parents=True, mode=0o700)
        outputs = session_root / OUTPUTS_DIR
        outputs.mkdir(mode=0o700)
        (session_root / HISTORY_DIR).mkdir(mode=0o700)
        (session_root / SKILL_DIR).mkdir(mode=0o700)
        try:
            self.prepare_sandbox(spec, container_name, workspace, outputs)
        except Exception:
            self.remove_container(container_name)
            self.remove_workspace(session_root)
            set_session_status(session_id, RunnerSession.Status.ERROR)
            raise
        set_command_timeout(session_id, self.has_timeout(container_name))
        set_session_status(session_id, RunnerSession.Status.ACTIVE)
        return session_id

    def has_timeout(self, container_name: str) -> bool:
        """Whether the sandbox image can run a command under timeout."""
        probe = self.run_command(
            ["docker", "exec", container_name, "timeout", "1", "true"]
        )
        return probe.returncode == 0

    def prepare_sandbox(
        self,
        spec: RunnerSessionSpec,
        container_name: str,
        workspace: Path,
        outputs: Path,
    ) -> None:
        """Unpack the commit, any earlier files and any skill from another
        repository, record the commit, and start the container."""
        unpack_source(spec.source_sha256, workspace)
        history = workspace.parent / HISTORY_DIR
        if spec.history_sha256:
            unpack_source(spec.history_sha256, history)
        skill = workspace.parent / SKILL_DIR
        if spec.skill_sha256:
            unpack_source(spec.skill_sha256, skill)
        if spec.collect_changes:
            record_baseline(workspace.parent, workspace)
        record_workspace_commit(workspace, spec.repository, spec.commit_sha)
        self.prepare_workspace_permissions(workspace)
        self.prepare_workspace_permissions(outputs)
        self.prepare_workspace_permissions(history)
        self.prepare_workspace_permissions(skill)
        completed = self.run_command(
            self.create_command(spec, container_name, workspace)
        )
        if completed.returncode:
            raise RunnerProtocolError("The runner could not create the sandbox.")
        completed = self.run_command(["docker", "start", container_name])
        if completed.returncode:
            raise RunnerProtocolError("The runner could not start the sandbox.")

    def execute(self, session_id: str, request: CommandRequest) -> CommandResult:
        cached = self.reserve_command(session_id, request)
        if cached is not None:
            return cached
        session = get_session(session_id)
        timeout = min(request.timeout_seconds, settings.RUNNER_COMMAND_TIMEOUT_SECONDS)
        argv = list(request.argv)
        runner_timeout = timeout
        if session.command_timeout:
            # timeout ends the command and its children inside the sandbox.
            # Stopping the sandbox would end the agent's whole run.
            argv = [
                "timeout",
                "-k",
                str(COMMAND_KILL_AFTER_SECONDS),
                str(timeout),
                *argv,
            ]
            runner_timeout = (
                timeout + COMMAND_KILL_AFTER_SECONDS + COMMAND_STOP_GRACE_SECONDS
            )
        command = [
            "docker",
            "exec",
            "--workdir",
            request.workdir,
            session.container_name,
            *argv,
        ]
        started = time.monotonic()
        result = self.run_captured(request.call_id, command, runner_timeout)
        # GNU timeout exits 124 or 137 and BusyBox with the signal's code, so
        # the time the command took is what shows that timeout ended it.
        ended_in_sandbox = bool(
            session.command_timeout
            and not result.timed_out
            and result.exit_code
            and time.monotonic() - started >= timeout
        )
        if ended_in_sandbox:
            result = replace(result, timed_out=True)
        complete_command(
            session,
            request.call_id,
            result.exit_code,
            result.output,
            result.timed_out,
        )
        if result.timed_out and not ended_in_sandbox:
            # The command outlived timeout, or the image has none.
            self.stop(session_id)
        elif result.exit_code:
            # gVisor ends the whole sandbox when it runs out of memory, so
            # later commands could never succeed. Docker sets OOMKilled at
            # once but reports the container running a little longer.
            if self.container_oom_killed(session.container_name):
                self.stop(session_id)
                raise RunnerProtocolError("The sandbox ran out of memory.")
            if not self.container_running(session.container_name):
                self.stop(session_id)
                raise RunnerProtocolError("The sandbox stopped unexpectedly.")
        return result

    def run_captured(
        self, call_id: str, command: list[str], timeout: int
    ) -> CommandResult:
        """Run a docker command, keeping the start and end of long output."""
        output_descriptor, output_name = tempfile.mkstemp(
            dir=settings.RUNNER_WORK_ROOT, prefix="command-output-"
        )
        output_path = Path(output_name)
        timed_out = False
        exit_code: int | None = None
        try:
            with os.fdopen(output_descriptor, "wb") as output:
                try:
                    completed = subprocess.run(
                        command,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        timeout=timeout,
                        check=False,
                    )
                    exit_code = completed.returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
            return CommandResult(
                call_id=call_id,
                exit_code=exit_code,
                output=self.read_output(output_path),
                timed_out=timed_out,
            )
        finally:
            output_path.unlink(missing_ok=True)

    def collect(self, session_id: str) -> Path:
        """Stop the sandbox and archive its changes and saved files.

        The sandbox stops first, so nothing changes the files while the runner
        reads them. Asking again returns the same archive.
        """
        try:
            session = get_session(session_id)
        except RunnerSession.DoesNotExist as error:
            raise RunnerProtocolError("Unknown runner session.") from error
        if session.status != RunnerSession.Status.ACTIVE:
            raise RunnerProtocolError("The session has ended, so its files are gone.")
        workspace = Path(session.workspace_path)
        session_root = workspace.parent.resolve()
        self.require_owned_path(session_root)
        self.remove_container(agent_name(session_id))
        self.remove_container(setup_name(session_id))
        self.run_command(["docker", "kill", session.container_name])
        if self.container_running(session.container_name):
            raise RunnerProtocolError("The runner could not stop the sandbox.")
        return collect(session_root, workspace)

    def stop(self, session_id: str) -> None:
        with transaction.atomic():
            session = lock_session(session_id)
            if session is None:
                return
            session.status = RunnerSession.Status.STOPPED
            session.save(update_fields=("status", "updated_at"))
            container_name = session.container_name
            workspace = Path(session.workspace_path)
        self.remove_container(agent_name(session_id))
        self.remove_container(setup_name(session_id))
        self.remove_container(container_name)
        self.remove_workspace(workspace.parent)

    def reserve_command(
        self, session_id: str, request: CommandRequest, setup: bool = False
    ) -> CommandResult | None:
        """Record a call before it runs, or return its earlier result.

        The agent's first call closes setup for good, so commands with network
        access never run once the agent could have read untrusted input.
        """
        if request.call_id.startswith(SETUP_CALL_PREFIX) != setup:
            raise RunnerProtocolError("Only setup commands may use setup call IDs.")
        with transaction.atomic():
            session = lock_session(session_id, active_only=True)
            if session is None:
                raise RunnerProtocolError("Unknown or inactive runner session.")
            if setup and session.setup_closed:
                raise RunnerProtocolError("Setup can't run once the agent has started.")
            if not setup and not session.setup_closed:
                session.setup_closed = True
                session.save(update_fields=("setup_closed", "updated_at"))
            command = session_command(session, request.call_id)
            if command is None:
                create_command(session, request.call_id, request.fingerprint)
                return None
            if command.fingerprint != request.fingerprint:
                raise RunnerProtocolError(
                    "A tool call ID was reused with different arguments."
                )
            if command.status != RunnerCommand.Status.COMPLETED:
                raise RunnerProtocolError(
                    "The previous execution outcome is unknown; it will not be repeated."
                )
            return CommandResult(
                call_id=command.call_id,
                exit_code=command.exit_code,
                output=command.output,
                timed_out=command.timed_out,
            )

    @staticmethod
    def require_matching_spec(session: RunnerSession, spec: RunnerSessionSpec) -> None:
        recorded = (
            session.run_id,
            session.repository,
            session.commit_sha,
            session.image,
            session.source_sha256,
            session.collect_changes,
            session.history_sha256,
            session.skill_sha256,
            tuple(session.setup),
        )
        requested = (
            spec.run_id,
            spec.repository,
            spec.commit_sha,
            spec.image,
            spec.source_sha256,
            spec.collect_changes,
            spec.history_sha256,
            spec.skill_sha256,
            spec.setup,
        )
        if recorded != requested:
            raise RunnerProtocolError("The run already has a different session spec.")

    @staticmethod
    def create_command(
        spec: RunnerSessionSpec, container_name: str, workspace: Path
    ) -> list[str]:
        return [
            "docker",
            "create",
            "--name",
            container_name,
            *sandbox_flags(),
            "--network=none",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=256m",
            # Bind mounts are writable by default. Docker rejects a bare "rw" field.
            f"--mount=type=bind,src={workspace},dst=/workspace",
            # Files the agent hands back. The runner collects them at the end.
            f"--mount=type=bind,src={workspace.parent / OUTPUTS_DIR},"
            f"dst={SANDBOX_OUTPUTS_DIR}",
            # Files earlier runs of the job saved. The sandbox can only read them.
            f"--mount=type=bind,src={workspace.parent / HISTORY_DIR},"
            f"dst={SANDBOX_HISTORY_DIR},readonly",
            # A skill from another repository. The sandbox can only read it,
            # and it is never part of the repository's changes.
            f"--mount=type=bind,src={workspace.parent / SKILL_DIR},"
            f"dst={SANDBOX_SKILL_DIR},readonly",
            "--workdir=/workspace",
            "--env=HOME=/tmp",
            # Skills that run Python must not leave bytecode in the repository.
            "--env=PYTHONDONTWRITEBYTECODE=1",
            # Docker Desktop shows bind-mounted files under another owner, and
            # git then refuses the workspace's repository. The agent owns every
            # file in the sandbox anyway, so git's ownership check adds nothing.
            "--env=GIT_CONFIG_COUNT=1",
            "--env=GIT_CONFIG_KEY_0=safe.directory",
            "--env=GIT_CONFIG_VALUE_0=/workspace",
            spec.image,
            "sleep",
            "infinity",
        ]

    @staticmethod
    def prepare_workspace_permissions(workspace: Path) -> None:
        uid = settings.RUNNER_SANDBOX_UID
        gid = settings.RUNNER_SANDBOX_GID
        for root, directories, files in os.walk(workspace):
            for name in [*directories, *files]:
                os.chown(Path(root) / name, uid, gid, follow_symlinks=False)
        os.chown(workspace, uid, gid, follow_symlinks=False)

    @staticmethod
    def read_output(path: Path) -> str:
        limit = settings.RUNNER_COMMAND_OUTPUT_MAX_BYTES
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size <= limit:
                content = stream.read()
            else:
                remaining = max(limit - len(TRUNCATION_MARKER), 0)
                head_size = remaining // 2
                tail_size = remaining - head_size
                head = stream.read(head_size)
                stream.seek(size - tail_size)
                content = head + TRUNCATION_MARKER + stream.read(tail_size)
        return content[:limit].decode(errors="replace")

    def container_exists(self, name: str) -> bool:
        return self.run_command(["docker", "inspect", name], timeout=10).returncode == 0

    def container_state(self, name: str, field: str) -> bool:
        completed = self.run_command(
            ["docker", "inspect", "--format", f"{{{{.State.{field}}}}}", name],
            timeout=10,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"

    def container_running(self, name: str) -> bool:
        return self.container_state(name, "Running")

    def container_oom_killed(self, name: str) -> bool:
        return self.container_state(name, "OOMKilled")

    def remove_container(self, name: str) -> None:
        self.run_command(["docker", "rm", "--force", name], timeout=30)

    def remove_workspace(self, path: Path) -> None:
        path = path.resolve()
        self.require_owned_path(path)
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def require_owned_path(path: Path) -> None:
        sessions_root = (settings.RUNNER_WORK_ROOT / "sessions").resolve()
        if path == sessions_root or not path.is_relative_to(sessions_root):
            raise RunnerProtocolError("Runner workspace escaped its owned root.")

    @staticmethod
    def run_command(
        command: list[str], timeout: int = 30
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


def get_runner_service() -> DockerRunnerService:
    return DockerRunnerService()
