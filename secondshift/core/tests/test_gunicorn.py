import runpy
from typing import Any

import pytest

from secondshift.config.constants import SERVICE_VAR, GunicornEnv, Service


def server_config(monkeypatch: pytest.MonkeyPatch, role: Service) -> dict[str, Any]:
    for name in GunicornEnv:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(SERVICE_VAR, role)
    return runpy.run_module("secondshift.config.gunicorn")


@pytest.mark.parametrize(
    ("role", "port", "workers", "factory", "graceful"),
    [
        (Service.WEB, 8000, 2, "wsgi", 30),
        (Service.GATEWAY, 8001, 2, "asgi", 600),
        (Service.RUNNER, 8002, 1, "wsgi", 30),
    ],
)
def test_role_defaults(
    monkeypatch: pytest.MonkeyPatch,
    role: Service,
    port: int,
    workers: int,
    factory: str,
    graceful: int,
) -> None:
    config = server_config(monkeypatch, role)
    assert config["bind"] == f"0.0.0.0:{port}"
    assert config["workers"] == workers
    assert config["wsgi_app"] == f"secondshift.config.app:{factory}('{role}')"
    assert config["graceful_timeout"] == graceful
    assert config["control_socket_disable"] is True
    assert config["worker_class"] == (
        "uvicorn_worker.UvicornWorker" if role == Service.GATEWAY else "sync"
    )


def test_environment_overrides_without_changing_service_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_config(monkeypatch, Service.RUNNER)
    overrides = {
        GunicornEnv.BIND: "127.0.0.1:9100",
        GunicornEnv.WORKERS: "3",
        GunicornEnv.TIMEOUT: "45",
        GunicornEnv.GRACEFUL_TIMEOUT: "90",
        GunicornEnv.LOG_LEVEL: "warning",
        GunicornEnv.ACCESS_LOG: "",
    }
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    config = runpy.run_module("secondshift.config.gunicorn")
    assert config["bind"] == "127.0.0.1:9100"
    assert config["workers"] == 3
    assert config["timeout"] == 45
    assert config["graceful_timeout"] == 90
    assert config["loglevel"] == "warning"
    assert config["accesslog"] is None
    assert config["wsgi_app"] == "secondshift.config.app:wsgi('runner')"


def test_worker_cannot_be_started_as_an_http_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="run_dbos_worker"):
        server_config(monkeypatch, Service.WORKER)


@pytest.mark.parametrize(
    "name", [GunicornEnv.WORKERS, GunicornEnv.TIMEOUT, GunicornEnv.GRACEFUL_TIMEOUT]
)
def test_nonpositive_limits_fail_before_server_start(
    monkeypatch: pytest.MonkeyPatch, name: GunicornEnv
) -> None:
    server_config(monkeypatch, Service.WEB)
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match="must be positive"):
        runpy.run_module("secondshift.config.gunicorn")


def test_old_http_variable_names_are_not_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GUNICORN_BIND", "127.0.0.1:9999")
    monkeypatch.setenv("GUNICORN_WORKERS", "9")
    monkeypatch.setenv("GUNICORN_TIMEOUT", "120")
    config = server_config(monkeypatch, Service.WEB)
    assert config["bind"] == "0.0.0.0:8000"
    assert config["workers"] == 2
    assert config["timeout"] == 30
