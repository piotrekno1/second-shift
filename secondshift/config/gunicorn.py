"""Shared Gunicorn options, configured by each process environment."""

import os

from environ import Env

from secondshift.config.constants import SERVICE_VAR, Service

service = Service(os.environ.get(SERVICE_VAR, Service.WEB))
if service == Service.WORKER:
    raise ValueError(
        "The worker role runs python manage.py run_dbos_worker, not Gunicorn"
    )
env = Env()

factory = "asgi" if service == Service.GATEWAY else "wsgi"
wsgi_app = f"secondshift.config.app:{factory}('{service}')"
worker_class = "uvicorn_worker.UvicornWorker" if service == Service.GATEWAY else "sync"
bind = env.str("SECONDSHIFT_HTTP_BIND", default="0.0.0.0:8000")
workers = env.int("SECONDSHIFT_HTTP_PROCESSES", default=2)
timeout = env.int("SECONDSHIFT_HTTP_TIMEOUT_SECONDS", default=30)
graceful_timeout = env.int("SECONDSHIFT_HTTP_GRACEFUL_TIMEOUT_SECONDS", default=30)
loglevel = env.str("SECONDSHIFT_HTTP_LOG_LEVEL", default="info").lower()
accesslog = env.str("SECONDSHIFT_HTTP_ACCESS_LOG", default="-") or None
if workers < 1 or timeout < 1 or graceful_timeout < 1:
    raise ValueError("Gunicorn worker count and timeouts must be positive")
# Services may share a data directory. Never compete for a shared control socket.
control_socket_disable = True
