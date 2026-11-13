"""Check how `secondshift up` decides whether its port is free."""

import socket
from typing import Any

import djclick as click
import pytest

from secondshift.config.constants import AppEnv
from secondshift.core.management.commands.up import check_port, check_service_ports

HOST = "127.0.0.1"


def port_in_time_wait() -> int:
    """Leave a closed connection in TIME_WAIT on a port nobody listens on.

    The side that closes first keeps TIME_WAIT, so the server side closes
    first, as it does when SecondShift stops.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        client = socket.create_connection((HOST, port))
        served, _ = listener.accept()
        served.close()
        client.recv(1)
        client.close()
    return port


def test_a_port_left_in_time_wait_counts_as_free() -> None:
    check_port(HOST, port_in_time_wait())


def test_a_port_with_a_listener_is_refused() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((HOST, 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        with pytest.raises(click.ClickException, match="Address already in use"):
            check_port(HOST, port)


def test_a_busy_runner_port_names_its_setting(settings: Any) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((HOST, 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        settings.RUNNER_SERVICE_URL = f"http://{HOST}:{port}"
        settings.MODEL_GATEWAY_URL = f"http://{HOST}:{free_port()}"

        with pytest.raises(click.ClickException, match=AppEnv.RUNNER_SERVICE_URL):
            check_service_ports()


def test_free_service_ports_pass(settings: Any) -> None:
    settings.RUNNER_SERVICE_URL = f"http://{HOST}:{free_port()}"
    settings.MODEL_GATEWAY_URL = f"http://{HOST}:{free_port()}"

    check_service_ports()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]
