"""Build the agent image from the files shipped in the package."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from secondshift.config.constants import LOCAL_AGENT_TAG

AGENT_IMAGE_DIR = Path(__file__).parent / "agent_image"
BUILD_SECONDS = 1800


def resolve_image_id(image: str) -> str:
    """The immutable ID behind a local image name."""
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    image_id = result.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Docker did not return an immutable agent image ID.")
    return image_id


def build_agent_image(tag: str = LOCAL_AGENT_TAG) -> str:
    """Build the pinned agent image and return its immutable ID.

    The build context holds only the agent files, laid out as in the checkout,
    so Docker never uploads the rest of the installed packages.
    """
    with tempfile.TemporaryDirectory(prefix="secondshift-agent-") as context:
        target = Path(context) / "secondshift" / "runner" / "agent_image"
        shutil.copytree(
            AGENT_IMAGE_DIR, target, ignore=shutil.ignore_patterns("__pycache__")
        )
        subprocess.run(
            [
                "docker",
                "build",
                "--quiet",
                "-f",
                str(target / "Dockerfile"),
                "-t",
                tag,
                context,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=BUILD_SECONDS,
        )
    return resolve_image_id(tag)
