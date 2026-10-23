"""First start of a development install: create the data folder and its keys."""

import fcntl
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


def write_generated_env(data_dir: Path) -> None:
    """Create the data folder with fresh keys in its .env, once.

    The runner gets only its token, in runner.env. A lock keeps processes
    starting together from writing different keys.
    """
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(data_dir / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (data_dir / ".env").exists():
            return
        token = secrets.token_urlsafe(48)
        files = {
            "runner.env": f"SECONDSHIFT_RUNNER_SERVICE_TOKEN={token}\n",
            ".env": (
                "# Generated on first start. Back it up: FIELD_ENCRYPTION_KEY\n"
                "# decrypts stored connection credentials.\n"
                f"DJANGO_SECRET_KEY={secrets.token_urlsafe(48)}\n"
                f"FIELD_ENCRYPTION_KEY={Fernet.generate_key().decode()}\n"
                f"SECONDSHIFT_RUNNER_SERVICE_TOKEN={token}\n"
            ),
        }
        for name, content in files.items():  # .env last: it marks completion
            descriptor = os.open(
                data_dir / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "w") as file:
                file.write(content)
