"""Run the docker command line and answer common questions about Docker."""

import re
import subprocess
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 30
INFO_TIMEOUT_SECONDS = 15
IMAGE_ID_PATTERN = r"sha256:[0-9a-f]{64}"
DOCKER_DESKTOP = "Docker Desktop"


class DockerError(Exception):
    """Docker failed, timed out, could not start or answered unexpectedly."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr.strip()

    @property
    def reason(self) -> str:
        """Docker's last line of output, or the message when it said nothing."""
        lines = [line for line in self.stderr.splitlines() if line.strip()]
        return lines[-1] if lines else str(self)


def run(
    *arguments: str, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Run docker and return its result, whatever its exit code.

    Raises DockerError only when docker times out or cannot start.
    """
    command = arguments[0] if arguments else ""
    try:
        return subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DockerError(f"docker {command} timed out.") from error
    except OSError as error:
        raise DockerError("Docker isn't installed.", str(error)) from error


def output(*arguments: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Docker's output, without the trailing newline. Raises DockerError."""
    result = run(*arguments, timeout=timeout)
    if result.returncode:
        command = arguments[0] if arguments else ""
        raise DockerError(f"docker {command} failed.", result.stderr or "")
    return result.stdout.strip()


def available() -> bool:
    """Whether Docker is installed and its daemon answers."""
    try:
        return run("info", timeout=INFO_TIMEOUT_SECONDS).returncode == 0
    except DockerError:
        return False


def operating_system() -> str:
    """What Docker runs on, like "Docker Desktop". Raises DockerError."""
    return output(
        "info", "--format", "{{.OperatingSystem}}", timeout=INFO_TIMEOUT_SECONDS
    )


def is_docker_desktop() -> bool:
    try:
        return operating_system() == DOCKER_DESKTOP
    except DockerError:
        return False


def resolve_image_id(image: str) -> str:
    """The immutable ID behind a local image name. Raises DockerError."""
    image_id = output("image", "inspect", "--format", "{{.Id}}", image)
    if not re.fullmatch(IMAGE_ID_PATTERN, image_id):
        raise DockerError("Docker did not return an immutable image ID.")
    return image_id


def build(dockerfile: Path, tag: str, context: Path, timeout: float) -> None:
    """Build an image quietly. Raises DockerError with Docker's output."""
    output(
        "build",
        "--quiet",
        "-f",
        str(dockerfile),
        "-t",
        tag,
        str(context),
        timeout=timeout,
    )
