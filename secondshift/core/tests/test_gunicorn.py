import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from secondshift.config.constants import (
    ENVIRONMENT_VAR,
    SERVICE_VAR,
    AppEnv,
    Environment,
    GunicornEnv,
    Service,
)


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
            ENVIRONMENT_VAR: Environment.PRODUCTION,
            SERVICE_VAR: role,
            AppEnv.DATA_DIR: str(directory),
            AppEnv.SECRET_KEY: "test-signing-key",
            AppEnv.ENCRYPTION_KEY: Fernet.generate_key().decode(),
            AppEnv.DATABASE_URL: "sqlite:///:memory:",
            AppEnv.PUBLIC_URL: "https://secondshift.example.com",
            AppEnv.RUNNER_RUNTIME: "runsc",
            AppEnv.RUNNER_SERVICE_TOKEN: "t" * 40,
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
        (Service.GATEWAY, "0.0.0.0:8001", 2, "asgi", 600),
        (Service.RUNNER, "0.0.0.0:8002", 1, "wsgi", 30),
    ],
)
def test_role_settings_reach_gunicorn(
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
            GunicornEnv.BIND: "127.0.0.1:9100",
            GunicornEnv.WORKERS: "3",
            GunicornEnv.TIMEOUT: "45",
            GunicornEnv.GRACEFUL_TIMEOUT: "90",
            GunicornEnv.LOG_LEVEL: "warning",
            GunicornEnv.ACCESS_LOG: "",
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
    "name", [GunicornEnv.WORKERS, GunicornEnv.TIMEOUT, GunicornEnv.GRACEFUL_TIMEOUT]
)
def test_nonpositive_limits_fail_before_server_start(
    tmp_path: Path, name: GunicornEnv
) -> None:
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
