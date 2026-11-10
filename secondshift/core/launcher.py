"""Start and stop the processes behind `secondshift up`.

The web app, model gateway, runner and worker run as separate processes, as
they do on a server. The runner keeps its own settings and gets none of the
control plane's keys.
"""

import ipaddress
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread
from types import FrameType
from typing import Any
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
from secondshift.core import local_output

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
    AppEnv.AGENT_IMAGE,
    AppEnv.AGENT_NETWORK,
    AppEnv.AGENT_BROKER_URL,
    AppEnv.AGENT_RESOLV_CONF,
    AppEnv.DATA_DIR,
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
        if name in RUNNER_ENV_NAMES or name.startswith(RUNNER_ENV_PREFIXES)
    }
    env[SETTINGS_MODULE_VAR] = RUNNER_SETTINGS
    env[AppEnv.RUNNER_RUNTIME] = SandboxRuntime.DOCKER
    return env


def public_url(host: str, port: int) -> str:
    """Where people open the app. 0.0.0.0 listens everywhere, so use loopback."""
    return f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"


def web_address(host: str, port: int, parent: dict[str, str]) -> tuple[str, int]:
    """Use an explicit HTTP bind for the local web process."""
    if bind := parent.get(AppEnv.HTTP_BIND):
        endpoint = urlsplit(f"http://{bind}")
        if not endpoint.hostname or endpoint.port is None:
            raise ValueError(f"{AppEnv.HTTP_BIND} must contain a host and port")
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
    host: str,
    port: int,
    parent: dict[str, str],
    python: str = sys.executable,
    agent_host: str | None = None,
) -> list[Process]:
    """The local processes. agent_host also opens the gateway to containers."""
    host, port = web_address(host, port, parent)
    gateway_url = settings.MODEL_GATEWAY_URL
    runner_url = settings.RUNNER_SERVICE_URL
    gateway_port = local_service_port(gateway_url)
    gateway_bind = ["127.0.0.1"]
    if agent_host:
        address = ipaddress.ip_address(agent_host)
        # 0.0.0.0 counts as private but listens on every network.
        if address.is_unspecified or not address.is_private:
            raise ValueError("The agent gateway address must be private.")
        gateway_bind.append(agent_host)
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
        service: Service,
        addresses: list[str],
        http_port: int,
        environment: dict[str, str],
    ) -> Process:
        environment = {
            **environment,
            SERVICE_VAR: service,
            AppEnv.HTTP_BIND: ",".join(f"{a}:{http_port}" for a in addresses),
            AppEnv.HTTP_LOG_LEVEL: "warning",
            AppEnv.HTTP_ACCESS_LOG: "",
        }
        if service in {Service.GATEWAY, Service.RUNNER}:
            environment[AppEnv.HTTP_PROCESSES] = "1"
        if service == Service.GATEWAY:
            environment[AppEnv.HTTP_GRACEFUL_TIMEOUT_SECONDS] = "600"
        return Process(
            service,
            [python, "-m", "gunicorn", "--config", GUNICORN_CONFIG],
            environment,
            f"http://127.0.0.1:{http_port}/-/health/",
        )

    return [
        http_service(
            Service.RUNNER,
            ["127.0.0.1"],
            runner_port,
            # Local mode lets the runner default its agent image and broker.
            {
                **runner_env(parent),
                ENVIRONMENT_VAR: Environment.LOCAL,
                AppEnv.MODEL_GATEWAY_URL: gateway_url,
            },
        ),
        http_service(Service.GATEWAY, gateway_bind, gateway_port, control),
        Process(
            Service.WORKER,
            [python, "-m", "secondshift", "run_dbos_worker"],
            {**control, SERVICE_VAR: Service.WORKER},
        ),
        http_service(Service.WEB, [host], port, control),
    ]


def is_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def is_ready(name: str, health_url: str | None) -> bool:
    """Web services answer a health check. The worker writes a ready file."""
    if health_url:
        return is_healthy(health_url)
    if name == Service.WORKER:
        return local_output.worker_ready_path().exists()
    return True


def run(
    services: list[Process],
    on_ready: Callable[[], None],
    echo: Callable[[str], None],
    start_timeout: float = 180,
    debug: bool = False,
    url: str | None = None,
) -> int:
    """Run every service until Ctrl+C or until one of them stops.

    With a url, the running instance is saved for `secondshift status`.
    """

    def stop_on_signal(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, stop_on_signal)
    processes: dict[str, subprocess.Popen[bytes]] = {}
    readers: list[Thread] = []
    local_output.worker_ready_path().unlink(missing_ok=True)
    try:
        for service in services:
            processes[service.name], reader = start_process(service, debug)
            readers.append(reader)
        if url:
            local_output.save_state(url, describe(services, processes))
        if not wait_until_ready(services, processes, start_timeout, echo):
            return 1
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
        for reader in readers:
            reader.join(timeout=2)
        if url:
            local_output.state_path().unlink(missing_ok=True)
        local_output.worker_ready_path().unlink(missing_ok=True)
        echo("SecondShift stopped.")
        signal.signal(signal.SIGTERM, previous_handler)


def wait_until_ready(
    services: list[Process],
    processes: dict[str, subprocess.Popen[bytes]],
    timeout: float,
    echo: Callable[[str], None],
) -> bool:
    waiting = services
    deadline = time.monotonic() + timeout
    while waiting := [s for s in waiting if not is_ready(s.name, s.health_url)]:
        if stopped := stopped_service(processes):
            echo(f"The {stopped} process stopped while starting.")
            return False
        if time.monotonic() > deadline:
            names = ", ".join(service.name for service in waiting)
            echo(f"Timed out waiting for: {names}.")
            return False
        time.sleep(0.5)
    return True


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
            process.wait()


def start_process(
    service: Process, debug: bool
) -> tuple[subprocess.Popen[bytes], Thread]:
    """Start a service with its output captured to its log file."""
    argv = service.argv
    if service.name == Service.WORKER:
        argv = [*argv, "--ready-file", str(local_output.worker_ready_path())]
    process = subprocess.Popen(
        argv,
        env={**service.env, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    return process, local_output.capture(process.stdout, service.name, debug)


def describe(
    services: list[Process], processes: dict[str, subprocess.Popen[bytes]]
) -> dict[str, dict[str, Any]]:
    return {
        service.name: {
            "pid": processes[service.name].pid,
            "health_url": service.health_url,
        }
        for service in services
    }
