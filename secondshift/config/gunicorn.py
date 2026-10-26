"""Shared HTTP server settings. Select the role with SECONDSHIFT_SERVICE."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from environ import Env

from secondshift.config.constants import (
    DEFAULT_HTTP_PORTS,
    ENVIRONMENT_VAR,
    SERVICE_VAR,
    AppEnv,
    Environment,
    GunicornEnv,
    Service,
)


class HttpEnv(Env):
    # Read HTTP options without adding application secrets to the runner process.
    ENVIRON = dict(os.environ)


env = HttpEnv()
environment = Environment(os.environ.get(ENVIRONMENT_VAR, Environment.PRODUCTION))
if environment == Environment.DEV:
    HttpEnv.read_env(Path(__file__).resolve().parents[2] / ".env")
service = Service(os.environ.get(SERVICE_VAR, Service.WEB))
# Worker count and graceful shutdown timeout
role_defaults = {
    Service.WEB: (2, 30),
    Service.GATEWAY: (2, 600),
    Service.RUNNER: (1, 30),
}
if service not in role_defaults:
    raise ValueError(
        "The worker role runs python manage.py run_dbos_worker, not Gunicorn"
    )
default_workers, default_graceful_timeout = role_defaults[service]
factory = "asgi" if service == Service.GATEWAY else "wsgi"
wsgi_app = f"secondshift.config.app:{factory}('{service}')"
worker_class = "uvicorn_worker.UvicornWorker" if service == Service.GATEWAY else "sync"
port = DEFAULT_HTTP_PORTS[service]
address = "0.0.0.0"
if environment in {Environment.DEV, Environment.LOCAL}:
    address = "127.0.0.1"
    endpoint_name = {
        Service.GATEWAY: AppEnv.MODEL_GATEWAY_URL,
        Service.RUNNER: AppEnv.RUNNER_SERVICE_URL,
    }.get(service)
    if endpoint_name is not None:
        endpoint = urlsplit(env.str(endpoint_name, default=""))
        port = endpoint.port or (80 if endpoint.scheme == "http" else port)
bind = env.str(GunicornEnv.BIND, default=f"{address}:{port}")
workers = env.int(GunicornEnv.WORKERS, default=default_workers)
timeout = env.int(GunicornEnv.TIMEOUT, default=30)
graceful_timeout = env.int(
    GunicornEnv.GRACEFUL_TIMEOUT, default=default_graceful_timeout
)
loglevel = env.str(GunicornEnv.LOG_LEVEL, default="info").lower()
accesslog = env.str(GunicornEnv.ACCESS_LOG, default="-") or None
# Services may share a data directory. Never compete for a shared control socket.
control_socket_disable = True
if workers < 1 or timeout < 1 or graceful_timeout < 1:
    raise ValueError("Gunicorn worker count and timeouts must be positive")
