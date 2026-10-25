import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction

from secondshift.automations.runner_protocol import (
    CommandRequest,
    CommandResult,
    RunnerBackend,
    RunnerProtocolError,
    RunnerSessionSpec,
)
from secondshift.config.constants import SandboxRuntime
from secondshift.runner.models import RunnerCommand, RunnerSession

TRUNCATION_MARKER = b"\n[... command output truncated by SecondShift ...]\n"


class RunnerConfigurationError(RuntimeError):
    pass


class DockerRunnerService(RunnerBackend):
    """Container operations and ORM-backed idempotency for the runner service."""

    def verify_configuration(self) -> None:
        for root in (settings.RUNNER_WORK_ROOT, settings.RUNNER_REPOSITORY_ROOT):
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.verify_runtime()
        self.ensure_image()

    def ensure_image(self) -> None:
        """Pull the sandbox image at startup so the first run doesn't wait on it."""
        image = settings.RUNNER_IMAGE
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
        if spec.image != settings.RUNNER_IMAGE:
            raise RunnerProtocolError(
                "The requested image is not enabled by this runner."
            )
        session_id = spec.session_id
        container_name = f"secondshift-{session_id}"
        session_root = (settings.RUNNER_WORK_ROOT / "sessions" / session_id).resolve()
        self.require_owned_path(session_root)
        workspace = session_root / "workspace"

        with transaction.atomic():
            try:
                session = RunnerSession.objects.select_for_update().get(
                    session_id=session_id
                )
            except RunnerSession.DoesNotExist:
                try:
                    session = RunnerSession.objects.create(
                        session_id=session_id,
                        run_id=spec.run_id,
                        repository=spec.repository,
                        commit_sha=spec.commit_sha,
                        image=spec.image,
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
        try:
            self.export_repository(spec, workspace)
            self.prepare_workspace_permissions(workspace)
            completed = self.run_command(
                self.create_command(spec, container_name, workspace)
            )
            if completed.returncode:
                raise RunnerProtocolError("The runner could not create the sandbox.")
            completed = self.run_command(["docker", "start", container_name])
            if completed.returncode:
                raise RunnerProtocolError("The runner could not start the sandbox.")
        except Exception:
            self.remove_container(container_name)
            self.remove_workspace(session_root)
            RunnerSession.objects.filter(session_id=session_id).update(
                status=RunnerSession.Status.ERROR
            )
            raise
        RunnerSession.objects.filter(session_id=session_id).update(
            status=RunnerSession.Status.ACTIVE
        )
        return session_id

    def execute(self, session_id: str, request: CommandRequest) -> CommandResult:
        cached = self.reserve_command(session_id, request)
        if cached is not None:
            return cached
        session = RunnerSession.objects.get(session_id=session_id)
        timeout = min(request.timeout_seconds, settings.RUNNER_COMMAND_TIMEOUT_SECONDS)
        workdir = "/workspace"
        if request.cwd != ".":
            workdir += f"/{request.cwd}"
        command = [
            "docker",
            "exec",
            "--workdir",
            workdir,
            session.container_name,
            *request.argv,
        ]
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
            result = CommandResult(
                call_id=request.call_id,
                exit_code=exit_code,
                output=self.read_output(output_path),
                timed_out=timed_out,
            )
            RunnerCommand.objects.filter(
                session=session, call_id=request.call_id
            ).update(
                status=RunnerCommand.Status.COMPLETED,
                exit_code=result.exit_code,
                output=result.output,
                timed_out=result.timed_out,
            )
            if timed_out:
                self.stop(session_id)
            return result
        finally:
            output_path.unlink(missing_ok=True)

    def stop(self, session_id: str) -> None:
        with transaction.atomic():
            try:
                session = RunnerSession.objects.select_for_update().get(
                    session_id=session_id
                )
            except RunnerSession.DoesNotExist:
                return
            session.status = RunnerSession.Status.STOPPED
            session.save(update_fields=("status", "updated_at"))
            container_name = session.container_name
            workspace = Path(session.workspace_path)
        self.remove_container(container_name)
        self.remove_workspace(workspace.parent)

    def reserve_command(
        self, session_id: str, request: CommandRequest
    ) -> CommandResult | None:
        with transaction.atomic():
            try:
                session = RunnerSession.objects.select_for_update().get(
                    session_id=session_id, status=RunnerSession.Status.ACTIVE
                )
            except RunnerSession.DoesNotExist as error:
                raise RunnerProtocolError(
                    "Unknown or inactive runner session."
                ) from error
            try:
                command = RunnerCommand.objects.get(
                    session=session, call_id=request.call_id
                )
            except RunnerCommand.DoesNotExist:
                RunnerCommand.objects.create(
                    session=session,
                    call_id=request.call_id,
                    fingerprint=request.fingerprint,
                )
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
        )
        requested = (spec.run_id, spec.repository, spec.commit_sha, spec.image)
        if recorded != requested:
            raise RunnerProtocolError("The run already has a different session spec.")

    def export_repository(self, spec: RunnerSessionSpec, workspace: Path) -> None:
        source = (settings.RUNNER_REPOSITORY_ROOT / spec.repository).resolve()
        if (
            not source.is_relative_to(settings.RUNNER_REPOSITORY_ROOT)
            or not source.is_dir()
        ):
            raise RunnerProtocolError("The repository is not available to this runner.")
        archive = workspace.parent / "repository.tar"
        verified = self.run_command(
            [
                "git",
                "-C",
                str(source),
                "cat-file",
                "-e",
                f"{spec.commit_sha}^{{commit}}",
            ],
            timeout=30,
        )
        if verified.returncode:
            raise RunnerProtocolError("The recorded commit is unavailable.")
        exported = self.run_command(
            [
                "git",
                "-C",
                str(source),
                "archive",
                "--format=tar",
                f"--output={archive}",
                spec.commit_sha,
            ],
            timeout=60,
        )
        if exported.returncode:
            raise RunnerProtocolError("The runner could not export the repository.")
        try:
            with tarfile.open(archive) as bundle:
                bundle.extractall(workspace, filter="data")
        finally:
            archive.unlink(missing_ok=True)

    @staticmethod
    def create_command(
        spec: RunnerSessionSpec, container_name: str, workspace: Path
    ) -> list[str]:
        command = ["docker", "create", "--name", container_name]
        if settings.RUNNER_RUNTIME != SandboxRuntime.DOCKER:
            command += ["--runtime", settings.RUNNER_RUNTIME]
        command += [
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            f"--pids-limit={settings.RUNNER_PIDS_LIMIT}",
            f"--memory={settings.RUNNER_MEMORY}",
            f"--cpus={settings.RUNNER_CPUS}",
            "--network=none",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=256m",
            # Bind mounts are writable by default; Docker rejects a bare "rw" field.
            f"--mount=type=bind,src={workspace},dst=/workspace",
            "--workdir=/workspace",
            f"--user={settings.RUNNER_SANDBOX_UID}:{settings.RUNNER_SANDBOX_GID}",
            "--env=HOME=/tmp",
            # Skills that run Python must not leave bytecode in the repository.
            "--env=PYTHONDONTWRITEBYTECODE=1",
            spec.image,
            "sleep",
            "infinity",
        ]
        return command

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
