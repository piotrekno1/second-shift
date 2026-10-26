"""First start of a development install: create the data folder and its keys."""

import fcntl
import os
import secrets
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

from secondshift.config.constants import AppEnv


def write_generated_env(data_dir: Path) -> None:
    """Create the data folder with fresh keys in its .env, once.

    The runner gets only its token, in runner.env. A lock keeps processes
    starting together from writing different keys.
    """
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(data_dir / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (data_dir / ".env").exists():
            migrate_generated_env(data_dir / ".env")
            return
        token = secrets.token_urlsafe(48)
        files = {
            "runner.env": f"SECONDSHIFT_RUNNER_SERVICE_TOKEN={token}\n",
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


def migrate_generated_env(path: Path) -> None:
    """Rename generated pre-SECONDSHIFT keys without rotating their values.

    Called under the data-folder lock. Replace atomically so concurrent startup
    cannot read a partially rewritten encryption key.
    """
    renamed = {
        "DJANGO_SECRET_KEY": AppEnv.SECRET_KEY,
        "FIELD_ENCRYPTION_KEY": AppEnv.ENCRYPTION_KEY,
    }
    lines = path.read_text().splitlines(keepends=True)
    names = {line.partition("=")[0] for line in lines}
    updated = [
        f"{renamed[name]}={value}"
        if separator and name in renamed and renamed[name] not in names
        else line
        for line in lines
        for name, separator, value in [line.partition("=")]
    ]
    if updated == lines:
        return
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.writelines(updated)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
