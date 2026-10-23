"""Start and stop the processes behind `secondshift up`.

The web app, model gateway, runner and worker run as separate processes, as
they do on a server. The runner keeps its own settings and gets none of the
control plane's keys.
"""

import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType

from secondshift.config.constants import (
    ENVIRONMENT_VAR,
    RUNNER_SETTINGS,
    SETTINGS_MODULE_VAR,
    Environment,
    SandboxRuntime,
    Service,
    settings_module,
)

CONTROL_SETTINGS = settings_module(Environment.LOCAL)
# The runner gets only these variables from the parent environment.
RUNNER_ENV_NAMES = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "LOG_LEVEL"}
RUNNER_ENV_PREFIXES = ("DOCKER_", "SECONDSHIFT_RUNNER_")
STOP_SECONDS = 15


@dataclass(frozen=True)
class Process:
    name: str
    argv: list[str]
    env: dict[str, str]
    health_url: str | None = None


def runner_env(parent: dict[str, str]) -> dict[str, str]:
    """The runner's environment: Docker access and its own settings, no keys."""
    env = {
        name: value
        for name, value in parent.items()
        if name in RUNNER_ENV_NAMES
        or name.startswith(RUNNER_ENV_PREFIXES)
        or name == "SECONDSHIFT_DATA_DIR"
    }
    env[SETTINGS_MODULE_VAR] = RUNNER_SETTINGS
    env["SECONDSHIFT_RUNNER_RUNTIME"] = SandboxRuntime.DOCKER
    return env


def services(
    host: str, port: int, parent: dict[str, str], python: str = sys.executable
) -> list[Process]:
    gateway_port, runner_port = port + 1, port + 2
    control = {
        **parent,
        ENVIRONMENT_VAR: Environment.LOCAL,
        SETTINGS_MODULE_VAR: CONTROL_SETTINGS,
        "SECONDSHIFT_RUNNER_SERVICE_URL": f"http://127.0.0.1:{runner_port}",
        "SECONDSHIFT_MODEL_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
    }
    if sys.platform == "darwin":
        # macOS stops forked processes that make HTTP calls without this.
        control["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
    # Several gunicorns run side by side, so none opens the shared control socket.
    gunicorn = [
        python,
        "-m",
        "gunicorn",
        "--log-level",
        "warning",
        "--no-control-socket",
    ]
    return [
        Process(
            Service.RUNNER,
            [
                *gunicorn,
                f"secondshift.config.app:wsgi('{Service.RUNNER}')",
                "--bind",
                f"127.0.0.1:{runner_port}",
                "--workers",
                "1",
            ],
            runner_env(parent),
            f"http://127.0.0.1:{runner_port}/-/health/",
        ),
        Process(
            Service.GATEWAY,
            [
                *gunicorn,
                f"secondshift.config.app:asgi('{Service.GATEWAY}')",
                "--bind",
                f"127.0.0.1:{gateway_port}",
                "--worker-class",
                "uvicorn_worker.UvicornWorker",
                "--workers",
                "1",
                "--graceful-timeout",
                "600",
            ],
            control,
            f"http://127.0.0.1:{gateway_port}/-/health/",
        ),
        Process(
            Service.WORKER, [python, "-m", "secondshift", "run_dbos_worker"], control
        ),
        Process(
            Service.WEB,
            [
                *gunicorn,
                f"secondshift.config.app:wsgi('{Service.WEB}')",
                "--bind",
                f"{host}:{port}",
                "--workers",
                "2",
            ],
            control,
            f"http://127.0.0.1:{port}/-/health/",
        ),
    ]


def is_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def run(
    services: list[Process],
    on_ready: Callable[[], None],
    echo: Callable[[str], None],
    start_timeout: float = 180,
) -> int:
    """Run every service until Ctrl+C or until one of them stops."""

    def stop_on_signal(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, stop_on_signal)
    processes: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for service in services:
            processes[service.name] = subprocess.Popen(service.argv, env=service.env)
        waiting = [service for service in services if service.health_url]
        deadline = time.monotonic() + start_timeout
        while waiting:
            if stopped := stopped_service(processes):
                echo(f"The {stopped} process stopped while starting.")
                return 1
            if time.monotonic() > deadline:
                names = ", ".join(service.name for service in waiting)
                echo(f"Timed out waiting for: {names}.")
                return 1
            waiting = [s for s in waiting if not is_healthy(s.health_url or "")]
            time.sleep(0.5)
        on_ready()
        while not (stopped := stopped_service(processes)):
            time.sleep(0.5)
        echo(f"The {stopped} process stopped, so SecondShift is stopping too.")
        return 1
    except KeyboardInterrupt:
        echo("Stopping SecondShift...")
        return 0
    finally:
        stop_all(processes)
        signal.signal(signal.SIGTERM, previous_handler)


def stopped_service(processes: dict[str, subprocess.Popen[bytes]]) -> str | None:
    return next(
        (name for name, process in processes.items() if process.poll() is not None),
        None,
    )


def stop_all(processes: dict[str, subprocess.Popen[bytes]]) -> None:
    for process in processes.values():
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + STOP_SECONDS
    for process in processes.values():
        try:
            process.wait(timeout=max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired:
            process.kill()
