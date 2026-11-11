"""Check how `secondshift up` decides whether its port is free."""

import socket

import djclick as click
import pytest

from secondshift.core.management.commands.up import check_port

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
