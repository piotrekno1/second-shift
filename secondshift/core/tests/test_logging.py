"""Exercise Django's two-stage logging setup in an isolated interpreter."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("service", ["web", "gateway", "worker", "runner"])
@pytest.mark.parametrize("level", ["DEBUG", "WARNING"])
def test_logging_routes_once_and_protects_sensitive_libraries(
    service: str, level: str
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"""
import logging
from django.utils.log import configure_logging
from secondshift.config.logging import logging_config
configure_logging('logging.config.dictConfig', logging_config({level!r}, {service!r}))
for name in ('secondshift.runs', 'django.request', 'django.server'):
    logger = logging.getLogger(name)
    logger.debug('debug-event')
    logger.warning('warning-event')
for name in ('httpx', 'httpcore'):
    logging.getLogger(name).info('sensitive-url')
    logging.getLogger(name).warning('client-warning')
logging.getLogger('mcp.client').error('sensitive-payload')
""",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stderr.splitlines()
    assert len(lines) == (8 if level == "DEBUG" else 5)
    for name in ("secondshift.runs", "django.request", "django.server"):
        assert sum(f"{name}: warning-event" in line for line in lines) == 1
        assert sum(f"{name}: debug-event" in line for line in lines) == (
            1 if level == "DEBUG" else 0
        )
    assert all(f"{service}[" in line for line in lines)
    assert all(line[:4].isdigit() for line in lines)
    assert "sensitive" not in result.stderr
    assert result.stdout == ""
