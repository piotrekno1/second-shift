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
from urllib.parse import urlsplit

from django.conf import settings

from secondshift.config.constants import (
    ENVIRONMENT_VAR,
    GUNICORN_CONFIG,
    RUNNER_SETTINGS,
    SERVICE_VAR,
    SETTINGS_MODULE_VAR,
    AppEnv,
    Environment,
    SandboxRuntime,
    Service,
    settings_module,
)

CONTROL_SETTINGS = settings_module(Environment.LOCAL)
# The runner gets only these variables from the parent environment.
RUNNER_ENV_NAMES = {
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    AppEnv.LOG_LEVEL,
}
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


def public_url(host: str, port: int) -> str:
    """Where people open the app. 0.0.0.0 listens everywhere, so use loopback."""
    return f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"


def web_address(host: str, port: int, parent: dict[str, str]) -> tuple[str, int]:
    """Use an explicit HTTP bind for the local web process."""
    if bind := parent.get("SECONDSHIFT_HTTP_BIND"):
        endpoint = urlsplit(f"http://{bind}")
        if not endpoint.hostname or endpoint.port is None:
            raise ValueError("SECONDSHIFT_HTTP_BIND must contain a host and port")
        return endpoint.hostname, endpoint.port
    return host, port


def local_service_port(url: str) -> int:
    """Local mode starts internal services on loopback only."""
    endpoint = urlsplit(url)
    if endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError(
            "Local service URLs must use http://localhost or http://127.0.0.1"
        )
    return endpoint.port or 80


def services(
    host: str, port: int, parent: dict[str, str], python: str = sys.executable
) -> list[Process]:
    host, port = web_address(host, port, parent)
    gateway_url = settings.MODEL_GATEWAY_URL
    runner_url = settings.RUNNER_SERVICE_URL
    gateway_port = local_service_port(gateway_url)
    runner_port = local_service_port(runner_url)
    control = {
        # Links in notifications, invites and OAuth callbacks use it.
        AppEnv.PUBLIC_URL: parent.get(AppEnv.PUBLIC_URL, public_url(host, port)),
        **parent,
        ENVIRONMENT_VAR: Environment.LOCAL,
        SETTINGS_MODULE_VAR: CONTROL_SETTINGS,
        AppEnv.RUNNER_SERVICE_URL: runner_url,
        AppEnv.MODEL_GATEWAY_URL: gateway_url,
    }
    if sys.platform == "darwin":
        # macOS stops forked processes that make HTTP calls without this.
        control["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

    def http_service(
        service: Service, address: str, http_port: int, environment: dict[str, str]
    ) -> Process:
        environment = {
            **environment,
            SERVICE_VAR: service,
            "SECONDSHIFT_HTTP_BIND": f"{address}:{http_port}",
            "SECONDSHIFT_HTTP_LOG_LEVEL": "warning",
            "SECONDSHIFT_HTTP_ACCESS_LOG": "",
        }
        if service in {Service.GATEWAY, Service.RUNNER}:
            environment["SECONDSHIFT_HTTP_PROCESSES"] = "1"
        if service == Service.GATEWAY:
            environment["SECONDSHIFT_HTTP_GRACEFUL_TIMEOUT_SECONDS"] = "600"
        return Process(
            service,
            [python, "-m", "gunicorn", "--config", GUNICORN_CONFIG],
            environment,
            f"http://127.0.0.1:{http_port}/-/health/",
        )

    return [
        http_service(Service.RUNNER, "127.0.0.1", runner_port, runner_env(parent)),
        http_service(Service.GATEWAY, "127.0.0.1", gateway_port, control),
        Process(
            Service.WORKER,
            [python, "-m", "secondshift", "run_dbos_worker"],
            {**control, SERVICE_VAR: Service.WORKER},
        ),
        http_service(Service.WEB, host, port, control),
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
