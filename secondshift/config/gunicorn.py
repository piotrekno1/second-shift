"""Shared Gunicorn options, configured by each process environment."""

import os

import django
from environ import Env
from gunicorn.arbiter import Arbiter

from secondshift.config.constants import (
    ENVIRONMENT_VAR,
    SERVICE_VAR,
    AppEnv,
    Environment,
    Service,
)
from secondshift.core.local_agents import with_container_binds

service = Service(os.environ.get(SERVICE_VAR, Service.WEB))
if service == Service.WORKER:
    raise ValueError(
        "The worker role runs python manage.py run_dbos_worker, not Gunicorn"
    )
env = Env()

# Both hold long requests open (model streams, agent runs) while serving others.
async_http = service in (Service.GATEWAY, Service.RUNNER)
factory = "asgi" if async_http else "wsgi"
wsgi_app = f"secondshift.config.app:{factory}('{service}')"
worker_class = "uvicorn_worker.UvicornWorker" if async_http else "sync"
# One address, or several separated by commas.
bind = env.list(AppEnv.HTTP_BIND, default=["0.0.0.0:8000"])
# In dev, agents reach the gateway through the egress proxy. `secondshift up`
# opens the same addresses itself.
if service == Service.GATEWAY and os.environ.get(ENVIRONMENT_VAR) == Environment.DEV:
    bind = with_container_binds(bind)
workers = env.int(AppEnv.HTTP_PROCESSES, default=2)
timeout = env.int(AppEnv.HTTP_TIMEOUT_SECONDS, default=30)
graceful_timeout = env.int(AppEnv.HTTP_GRACEFUL_TIMEOUT_SECONDS, default=30)
loglevel = env.str(AppEnv.HTTP_LOG_LEVEL, default="info").lower()
accesslog = env.str(AppEnv.HTTP_ACCESS_LOG, default="-") or None
if workers < 1 or timeout < 1 or graceful_timeout < 1:
    raise ValueError("Gunicorn worker count and timeouts must be positive")
# Services may share a data directory. Never compete for a shared control socket.
control_socket_disable = True


def on_exit(server: Arbiter) -> None:
    """A local runner removes the egress proxy it started. Others have none."""
    if service != Service.RUNNER:
        return
    django.setup()
    from secondshift.runner.egress import stop_local_proxy  # noqa: PLC0415

    stop_local_proxy()
