"""Start the development runner with the exact locally built agent image."""

import os
import sys

from secondshift.config.constants import AppEnv
from secondshift.runner.images import resolve_image_id


def main() -> None:
    image = os.environ.get(AppEnv.AGENT_IMAGE, "")
    if not image:
        raise ValueError("The development agent image must be configured.")
    os.environ[AppEnv.AGENT_IMAGE] = resolve_image_id(image)
    os.execv(sys.executable, [sys.executable, "-m", "gunicorn", "--reload"])


if __name__ == "__main__":
    main()
