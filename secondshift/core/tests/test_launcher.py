import sys
import time

import click
import pytest
from django.core.management import call_command

from secondshift.core import launcher

PARENT = {
    "PATH": "/usr/bin",
    "HOME": "/home/me",
    "DOCKER_HOST": "unix:///var/run/docker.sock",
    "SECONDSHIFT_DATA_DIR": "/data",
    "SECONDSHIFT_RUNNER_SERVICE_TOKEN": "t" * 40,
    "DJANGO_SECRET_KEY": "secret",
    "FIELD_ENCRYPTION_KEY": "key",
    "DATABASE_URL": "postgres://user:pass@db/app",
    "GITHUB_CLIENT_SECRET": "github",
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
    }


def test_services_listen_next_to_the_chosen_port() -> None:
    services = by_name(9000)

    assert "127.0.0.1:9000" in services["web"].argv
    assert "127.0.0.1:9001" in services["gateway"].argv
    assert "127.0.0.1:9002" in services["runner"].argv
    for name in ("web", "gateway", "worker"):
        env = services[name].env
        assert env["DJANGO_ENV"] == "local"
        assert env["SECONDSHIFT_RUNNER_SERVICE_URL"] == "http://127.0.0.1:9002"
        assert env["SECONDSHIFT_MODEL_GATEWAY_URL"] == "http://127.0.0.1:9001"
        assert env["FRONTEND_URL"] == "http://127.0.0.1:9000"


def test_frontend_url_follows_the_address_unless_set() -> None:
    shared = launcher.services("0.0.0.0", 8000, PARENT, python="python")
    chosen = launcher.services(
        "0.0.0.0", 8000, {**PARENT, "FRONTEND_URL": "http://box.lan:8000"}
    )

    assert {s.env.get("FRONTEND_URL") for s in shared if s.name != "runner"} == {
        "http://127.0.0.1:8000"
    }
    assert {s.env.get("FRONTEND_URL") for s in chosen if s.name != "runner"} == {
        "http://box.lan:8000"
    }


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
    with pytest.raises(click.ClickException, match="DJANGO_ENV=local"):
        call_command("up")
