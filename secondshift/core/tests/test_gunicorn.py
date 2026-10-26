import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from secondshift.config.constants import (
    SERVICE_VAR,
    AppEnv,
    Service,
)
from secondshift.config.container_env import generate_environment


def server_config(
    role: Service, directory: Path, overrides: dict[str, str] | None = None
) -> dict[str, Any]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("SECONDSHIFT_") and name != "DJANGO_SETTINGS_MODULE"
    }
    environment.update(
        {
            SERVICE_VAR: role,
            AppEnv.DATA_DIR: str(directory),
            **(overrides or {}),
        }
    )
    output = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, runpy; "
            "config = runpy.run_module('secondshift.config.gunicorn'); "
            "print(json.dumps({name: config[name] for name in "
            "['bind', 'workers', 'wsgi_app', 'worker_class', 'timeout', "
            "'graceful_timeout', 'loglevel', 'accesslog', 'control_socket_disable']}))",
        ],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(output.stdout)


@pytest.mark.parametrize(
    ("role", "bind", "workers", "factory", "graceful"),
    [
        (Service.WEB, "0.0.0.0:8000", 2, "wsgi", 30),
        (Service.GATEWAY, "0.0.0.0:8000", 2, "asgi", 30),
        (Service.RUNNER, "0.0.0.0:8000", 2, "wsgi", 30),
    ],
)
def test_uniform_defaults_for_http_roles(
    tmp_path: Path, role: Service, bind: str, workers: int, factory: str, graceful: int
) -> None:
    config = server_config(role, tmp_path)
    assert config["bind"] == bind
    assert config["workers"] == workers
    assert config["wsgi_app"] == f"secondshift.config.app:{factory}('{role}')"
    assert config["graceful_timeout"] == graceful
    assert config["control_socket_disable"] is True
    assert config["worker_class"] == (
        "uvicorn_worker.UvicornWorker" if role == Service.GATEWAY else "sync"
    )


@pytest.mark.parametrize("role", [Service.WEB, Service.GATEWAY, Service.RUNNER])
def test_environment_overrides_reach_each_service(
    tmp_path: Path, role: Service
) -> None:
    config = server_config(
        role,
        tmp_path,
        {
            "SECONDSHIFT_HTTP_BIND": "127.0.0.1:9100",
            "SECONDSHIFT_HTTP_PROCESSES": "3",
            "SECONDSHIFT_HTTP_TIMEOUT_SECONDS": "45",
            "SECONDSHIFT_HTTP_GRACEFUL_TIMEOUT_SECONDS": "90",
            "SECONDSHIFT_HTTP_LOG_LEVEL": "warning",
            "SECONDSHIFT_HTTP_ACCESS_LOG": "",
        },
    )
    assert config["bind"] == "127.0.0.1:9100"
    assert config["workers"] == 3
    assert config["timeout"] == 45
    assert config["graceful_timeout"] == 90
    assert config["loglevel"] == "warning"
    assert config["accesslog"] is None


def test_worker_cannot_be_started_as_an_http_service(tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError) as error:
        server_config(Service.WORKER, tmp_path)
    assert "run_dbos_worker" in error.value.stderr


@pytest.mark.parametrize(
    "name",
    [
        "SECONDSHIFT_HTTP_PROCESSES",
        "SECONDSHIFT_HTTP_TIMEOUT_SECONDS",
        "SECONDSHIFT_HTTP_GRACEFUL_TIMEOUT_SECONDS",
    ],
)
def test_nonpositive_limits_fail_before_server_start(tmp_path: Path, name: str) -> None:
    with pytest.raises(subprocess.CalledProcessError) as error:
        server_config(Service.WEB, tmp_path, {name: "0"})
    assert "must be positive" in error.value.stderr


def test_old_http_variable_names_are_not_aliases(tmp_path: Path) -> None:
    config = server_config(
        Service.WEB,
        tmp_path,
        {"GUNICORN_BIND": "127.0.0.1:9999", "GUNICORN_WORKERS": "9"},
    )
    assert config["bind"] == "0.0.0.0:8000"
    assert config["workers"] == 2


@pytest.mark.parametrize(
    ("role", "bind", "processes", "graceful"),
    [
        (Service.WEB, "0.0.0.0:8000", 2, 30),
        (Service.GATEWAY, "0.0.0.0:8001", 2, 600),
        (Service.RUNNER, "0.0.0.0:8002", 1, 30),
    ],
)
def test_generated_env_files_configure_production_roles(
    tmp_path: Path, role: Service, bind: str, processes: int, graceful: int
) -> None:
    directory = tmp_path / "deployment"
    generate_environment("app.example", directory)
    values = dict(
        line.split("=", 1)
        for line in (directory / f"{role}.env").read_text().splitlines()
    )
    config = server_config(role, tmp_path, values)
    assert config["bind"] == bind
    assert config["workers"] == processes
    assert config["graceful_timeout"] == graceful
