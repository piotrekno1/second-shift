"""Build the agent image from the files shipped in the package."""

import argparse
import shutil
import tempfile
from pathlib import Path

from secondshift.config.constants import DEV_AGENT_TAG, LOCAL_AGENT_TAG
from secondshift.docker import commands

AGENT_IMAGE_DIR = Path(__file__).parent / "image"
BUILD_SECONDS = 1800


def build_agent_image(tag: str = LOCAL_AGENT_TAG) -> str:
    """Build the pinned agent image and return its immutable ID.

    Raises DockerError when Docker cannot build or inspect it.

    The build context holds only the agent files, laid out as in the checkout,
    so Docker never uploads the rest of the installed packages.
    """
    with tempfile.TemporaryDirectory(prefix="secondshift-agent-") as context:
        target = Path(context) / "secondshift" / "agent" / "image"
        shutil.copytree(
            AGENT_IMAGE_DIR, target, ignore=shutil.ignore_patterns("__pycache__")
        )
        commands.build(target / "Dockerfile", tag, Path(context), BUILD_SECONDS)
    return commands.resolve_image_id(tag)


def main(argv: list[str] | None = None) -> None:
    """Print the built image's ID.

    process-compose builds the development tag. A production server runs this
    in its runner container, so the agent image matches the running release.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=DEV_AGENT_TAG)
    print(build_agent_image(parser.parse_args(argv).tag))


if __name__ == "__main__":
    main()
