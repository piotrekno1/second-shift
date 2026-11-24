"""Generate private per-role env files for a new container installation."""

import argparse
import os
import re
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from secondshift.config.constants import (
    ENVIRONMENT_VAR,
    SERVICE_VAR,
    AppEnv,
    Environment,
    SandboxRuntime,
    Service,
)
from secondshift.config.first_run import default_runner_images

ROLE_FILES = {role: f"{role}.env" for role in Service}


def prepare_directory(hostname: str, directory: Path) -> Path:
    if len(hostname) > 253 or not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise ValueError(
            "Use a DNS hostname without a scheme, port, path or credentials"
        )
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Never overwrite existing configuration or replace its keys.
    if any((directory / name).exists() for name in [*ROLE_FILES.values(), ".env"]):
        raise FileExistsError(
            "Configuration already exists; reuse it rather than generating new keys"
        )
    return directory


def runner_values(token: str, allowed_host: str, directory: Path) -> dict[str, str]:
    runner = directory / "runner"
    runner.mkdir(mode=0o700, exist_ok=True)
    return {
        AppEnv.HTTP_BIND: "0.0.0.0:8002",
        AppEnv.HTTP_PROCESSES: "1",
        AppEnv.HTTP_TIMEOUT_SECONDS: "180",
        AppEnv.RUNNER_ALLOWED_HOSTS: allowed_host,
        AppEnv.RUNNER_SERVICE_TOKEN: token,
        AppEnv.RUNNER_RUNTIME: SandboxRuntime.GVISOR,
        AppEnv.DATA_DIR: str(runner),
        AppEnv.RUNNER_WORK_ROOT: str(runner),
    }


def write_env(directory: Path, role: Service, values: dict[str, str]) -> None:
    descriptor = os.open(
        directory / ROLE_FILES[role], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "w") as output:
        output.write(f"{ENVIRONMENT_VAR}={Environment.PRODUCTION}\n")
        output.write(f"{SERVICE_VAR}={role}\n")
        for name, value in values.items():
            output.write(f"{name}={value}\n")
        if role == Service.RUNNER:
            output.write("\n" + default_runner_images())


def generate_runner_environment(hostname: str, directory: Path) -> None:
    """Only runner.env, for a runner on another host. The control plane
    registers it with the token in that file."""
    directory = prepare_directory(hostname, directory)
    token = secrets.token_urlsafe(48)
    write_env(
        directory, Service.RUNNER, runner_values(token, hostname.lower(), directory)
    )


def generate_environment(hostname: str, directory: Path) -> None:
    directory = prepare_directory(hostname, directory)
    common = {
        AppEnv.SECRET_KEY: secrets.token_urlsafe(48),
        AppEnv.ENCRYPTION_KEY: Fernet.generate_key().decode(),
        AppEnv.DATABASE_URL: "sqlite:////var/lib/secondshift/control.sqlite3",
    }
    public = {
        AppEnv.PUBLIC_URL: f"https://{hostname.lower()}",
        AppEnv.RUNNER_RUNTIME: SandboxRuntime.GVISOR,
    }
    token = secrets.token_urlsafe(48)
    configs = {
        Service.WEB: {
            **common,
            **public,
            AppEnv.RUNNER_SERVICE_URL: "http://secondshift-runner:8002",
            AppEnv.RUNNER_SERVICE_TOKEN: token,
            AppEnv.HTTP_TIMEOUT_SECONDS: "300",
        },
        Service.GATEWAY: {
            **common,
            # The agent broker runs workspace tools for subscription runs.
            AppEnv.RUNNER_SERVICE_URL: "http://secondshift-runner:8002",
            AppEnv.RUNNER_SERVICE_TOKEN: token,
            AppEnv.ALLOWED_HOSTS: "secondshift-gateway",
            AppEnv.HTTP_BIND: "0.0.0.0:8001",
            AppEnv.HTTP_GRACEFUL_TIMEOUT_SECONDS: "600",
        },
        Service.WORKER: {
            **common,
            **public,
            AppEnv.RUNNER_SERVICE_URL: "http://secondshift-runner:8002",
            AppEnv.RUNNER_SERVICE_TOKEN: token,
            AppEnv.MODEL_GATEWAY_URL: "http://secondshift-gateway:8001",
        },
        Service.RUNNER: runner_values(token, "secondshift-runner", directory),
    }
    for role, values in configs.items():
        write_env(directory, role, values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hostname", required=True, help="Public DNS hostname, without https://"
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path.cwd(),
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--runner",
        action="store_true",
        help="Only runner.env, for a runner on another host. --hostname is its own.",
    )
    args = parser.parse_args()
    generate = generate_runner_environment if args.runner else generate_environment
    try:
        generate(args.hostname, args.directory)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    if args.runner:
        print("Created runner.env. Keep it private.")
    else:
        print(
            "Created web.env, gateway.env, worker.env and runner.env. "
            "Keep these files private."
        )


if __name__ == "__main__":
    main()
