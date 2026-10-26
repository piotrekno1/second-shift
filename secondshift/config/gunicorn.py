"""Shared HTTP server settings. Select the role with SECONDSHIFT_SERVICE."""

import os

from environ import Env

from secondshift.config.constants import SERVICE_VAR, GunicornEnv, Service

env = Env()
service = Service(os.environ.get(SERVICE_VAR, Service.WEB))
# port, worker count, graceful shutdown timeout
role_defaults = {
    Service.WEB: (8000, 2, 30),
    Service.GATEWAY: (8001, 2, 600),
    Service.RUNNER: (8002, 1, 30),
}
if service not in role_defaults:
    raise ValueError(
        "The worker role runs python manage.py run_dbos_worker, not Gunicorn"
    )
port, default_workers, default_graceful_timeout = role_defaults[service]
factory = "asgi" if service == Service.GATEWAY else "wsgi"
wsgi_app = f"secondshift.config.app:{factory}('{service}')"
worker_class = "uvicorn_worker.UvicornWorker" if service == Service.GATEWAY else "sync"
bind = env.str(GunicornEnv.BIND, default=f"0.0.0.0:{port}")
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
