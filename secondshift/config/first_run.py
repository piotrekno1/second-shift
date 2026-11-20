"""First start of a development install: create the data folder and its keys."""

import fcntl
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from secondshift.config.constants import AppEnv

# The sandbox images a new runner.env starts with. Only this file names them.
RUNNER_IMAGES = Path(__file__).with_name("runner_images.env")


def default_runner_images() -> str:
    return RUNNER_IMAGES.read_text()


def add_runner_images(runner_env: Path) -> None:
    """Give a runner.env from before 0.1.0b13 the default images, once.

    Images come only from env now, and that file has none. Once it names a
    default image, it is the person's to edit and is never touched again.
    """
    content = runner_env.read_text()
    if any(line.startswith(f"{AppEnv.RUNNER_IMAGE}=") for line in content.splitlines()):
        return
    with runner_env.open("a") as file:
        file.write(("\n" if content and not content.endswith("\n") else "") + "\n")
        file.write(default_runner_images())


def write_generated_env(data_dir: Path) -> None:
    """Create the data folder with fresh keys in its .env, once.

    The runner gets its token and the sandbox images, in runner.env. A lock
    keeps processes starting together from writing different keys.
    """
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(data_dir / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (data_dir / ".env").exists():
            add_runner_images(data_dir / "runner.env")
            return
        token = secrets.token_urlsafe(48)
        files = {
            "runner.env": (
                f"SECONDSHIFT_RUNNER_SERVICE_TOKEN={token}\n\n"
                + default_runner_images()
            ),
            ".env": (
                "# Generated on first start. Back it up: SECONDSHIFT_ENCRYPTION_KEY\n"
                "# decrypts stored connection credentials.\n"
                f"{AppEnv.SECRET_KEY}={secrets.token_urlsafe(48)}\n"
                f"{AppEnv.ENCRYPTION_KEY}={Fernet.generate_key().decode()}\n"
                f"SECONDSHIFT_RUNNER_SERVICE_TOKEN={token}\n"
            ),
        }
        for name, content in files.items():  # .env last: it marks completion
            descriptor = os.open(
                data_dir / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "w") as file:
                file.write(content)
