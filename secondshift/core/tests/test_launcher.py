import sys
import time

import click
import pytest
from django.core.management import call_command
from django.test import override_settings

from secondshift.core import launcher

PARENT = {
    "PATH": "/usr/bin",
    "HOME": "/home/me",
    "DOCKER_HOST": "unix:///var/run/docker.sock",
    "SECONDSHIFT_DATA_DIR": "/data",
    "SECONDSHIFT_RUNNER_SERVICE_TOKEN": "t" * 40,
    "SECONDSHIFT_SECRET_KEY": "secret",
    "SECONDSHIFT_ENCRYPTION_KEY": "key",
    "SECONDSHIFT_DATABASE_URL": "postgres://user:pass@db/app",
    "GITHUB_CLIENT_SECRET": "github",
    "SECONDSHIFT_GITHUB_APP_PRIVATE_KEY": "github-private-key",
    "SECONDSHIFT_GITHUB_APP_CLIENT_SECRET": "github-app-secret",
}


def by_name(port: int = 8000) -> dict[str, launcher.Process]:
    services = launcher.services("127.0.0.1", port, PARENT, python="python")
    return {service.name: service for service in services}


def test_runner_gets_docker_access_and_its_token_but_no_control_plane_keys() -> None:
    runner = by_name()["runner"]

    assert runner.env == {
        "PATH": "/usr/bin",
        "HOME": "/home/me",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "SECONDSHIFT_DATA_DIR": "/data",
        "SECONDSHIFT_RUNNER_SERVICE_TOKEN": "t" * 40,
        "DJANGO_SETTINGS_MODULE": "secondshift.config.settings.runner",
        "SECONDSHIFT_RUNNER_RUNTIME": "docker",
        "SECONDSHIFT_SERVICE": "runner",
        "SECONDSHIFT_HTTP_BIND": "127.0.0.1:8002",
        "SECONDSHIFT_HTTP_PROCESSES": "1",
        "SECONDSHIFT_HTTP_LOG_LEVEL": "warning",
        "SECONDSHIFT_HTTP_ACCESS_LOG": "",
    }


@override_settings(
    MODEL_GATEWAY_URL="http://127.0.0.1:9001",
    RUNNER_SERVICE_URL="http://127.0.0.1:9002",
)
def test_services_use_configured_internal_addresses() -> None:
    services = by_name(9000)

    assert services["web"].env["SECONDSHIFT_HTTP_BIND"] == "127.0.0.1:9000"
    assert services["gateway"].env["SECONDSHIFT_HTTP_BIND"] == "127.0.0.1:9001"
    assert services["runner"].env["SECONDSHIFT_HTTP_BIND"] == "127.0.0.1:9002"
    for name in ("web", "gateway", "worker"):
        env = services[name].env
        assert env["SECONDSHIFT_ENV"] == "local"
        assert env["SECONDSHIFT_RUNNER_SERVICE_URL"] == "http://127.0.0.1:9002"
        assert env["SECONDSHIFT_MODEL_GATEWAY_URL"] == "http://127.0.0.1:9001"
        assert env["SECONDSHIFT_PUBLIC_URL"] == "http://127.0.0.1:9000"


def test_frontend_url_follows_the_address_unless_set() -> None:
    shared = launcher.services("0.0.0.0", 8000, PARENT, python="python")
    chosen = launcher.services(
        "0.0.0.0", 8000, {**PARENT, "SECONDSHIFT_PUBLIC_URL": "http://box.lan:8000"}
    )

    assert {
        s.env.get("SECONDSHIFT_PUBLIC_URL") for s in shared if s.name != "runner"
    } == {"http://127.0.0.1:8000"}
    assert {
        s.env.get("SECONDSHIFT_PUBLIC_URL") for s in chosen if s.name != "runner"
    } == {"http://box.lan:8000"}


def test_one_process_stopping_stops_the_others() -> None:
    sleeper = launcher.Process(
        "sleeper", [sys.executable, "-c", "import time; time.sleep(60)"], {}
    )
    quitter = launcher.Process("quitter", [sys.executable, "-c", "pass"], {})
    messages: list[str] = []
    started = time.monotonic()

    code = launcher.run([sleeper, quitter], lambda: None, messages.append)

    assert code == 1
    assert time.monotonic() - started < 20
    assert messages == ["The quitter process stopped, so SecondShift is stopping too."]


def test_up_runs_only_in_the_local_environment() -> None:
    with pytest.raises(click.ClickException, match="SECONDSHIFT_ENV=local"):
        call_command("up")


def test_control_secrets_stay_out_of_runner_and_runner_database_is_forwarded() -> None:
    parent = {
        **PARENT,
        "SECONDSHIFT_SECRET_KEY": "private-signing-key",
        "SECONDSHIFT_ENCRYPTION_KEY": "private-encryption-key",
        "SECONDSHIFT_DATABASE_URL": "postgres://control:password@database/app",
        "SECONDSHIFT_RUNNER_DATABASE_URL": "sqlite:///runner-only.sqlite3",
    }
    runner = next(
        service
        for service in launcher.services("127.0.0.1", 8000, parent)
        if service.name == "runner"
    )
    assert (
        runner.env["SECONDSHIFT_RUNNER_DATABASE_URL"]
        == parent["SECONDSHIFT_RUNNER_DATABASE_URL"]
    )
    for name in (
        "SECONDSHIFT_SECRET_KEY",
        "SECONDSHIFT_ENCRYPTION_KEY",
        "SECONDSHIFT_DATABASE_URL",
    ):
        assert name not in runner.env


@override_settings(
    MODEL_GATEWAY_URL="http://localhost:9501",
    RUNNER_SERVICE_URL="http://localhost:9502",
)
def test_configured_ports_drive_local_services_and_health_checks() -> None:
    parent = {
        **PARENT,
        "SECONDSHIFT_HTTP_BIND": "127.0.0.1:9400",
        "SECONDSHIFT_MODEL_GATEWAY_URL": "http://localhost:9501",
        "SECONDSHIFT_RUNNER_SERVICE_URL": "http://localhost:9502",
    }
    services = {
        service.name: service
        for service in launcher.services("127.0.0.1", 9000, parent)
    }
    for name, port in [("web", 9400), ("gateway", 9501), ("runner", 9502)]:
        assert services[name].env["SECONDSHIFT_HTTP_BIND"] == f"127.0.0.1:{port}"
        assert services[name].health_url == f"http://127.0.0.1:{port}/-/health/"
    assert (
        services["worker"].env["SECONDSHIFT_MODEL_GATEWAY_URL"]
        == parent["SECONDSHIFT_MODEL_GATEWAY_URL"]
    )
    assert (
        services["worker"].env["SECONDSHIFT_RUNNER_SERVICE_URL"]
        == parent["SECONDSHIFT_RUNNER_SERVICE_URL"]
    )


@override_settings(RUNNER_SERVICE_URL="http://external:9502")
def test_local_services_reject_external_runner_address() -> None:
    with pytest.raises(ValueError, match="Local service URLs"):
        launcher.services(
            "127.0.0.1",
            9000,
            {**PARENT, "SECONDSHIFT_RUNNER_SERVICE_URL": "http://external:9502"},
        )
