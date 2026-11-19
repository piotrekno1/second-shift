"""DBOS steps and workflows that start with a working database connection.

Django checks a connection's health only when close_old_connections runs,
which a web request does at its start and end. Worker threads have no
requests, so a connection Postgres closed, after a restart for example,
stayed broken on its thread until the worker restarted. Every step and
workflow goes through these decorators, so each one checks first.
"""

import functools
from collections.abc import Callable
from typing import Any

from dbos import DBOS
from django.db import close_old_connections, connections


def refresh_connections() -> None:
    """Drop this thread's broken or expired connections.

    Skipped inside a transaction, which must keep its connection.
    """
    if not any(connection.in_atomic_block for connection in connections.all()):
        close_old_connections()


def fresh_connection[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        refresh_connections()
        return function(*args, **kwargs)

    return wrapper


def step[**P, R](**options: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        return DBOS.step(**options)(fresh_connection(function))

    return decorate


def workflow[**P, R](**options: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        return DBOS.workflow(**options)(fresh_connection(function))

    return decorate
