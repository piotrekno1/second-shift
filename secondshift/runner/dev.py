"""Start the development runner with the exact locally built agent image."""

import os
import re
import subprocess
import sys

from secondshift.config.constants import AppEnv


def resolve_image_id(image: str) -> str:
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


def main() -> None:
    image = os.environ.get(AppEnv.AGENT_IMAGE, "")
    if not image:
        raise ValueError("The development agent image must be configured.")
    os.environ[AppEnv.AGENT_IMAGE] = resolve_image_id(image)
    os.execv(sys.executable, [sys.executable, "-m", "gunicorn", "--reload"])


if __name__ == "__main__":
    main()
