"""Start each HTTP service using its Django settings."""

import os

from django.conf import settings

from secondshift.config.app import configure
from secondshift.config.constants import SERVICE_VAR, Service

service = Service(os.environ.get(SERVICE_VAR, Service.WEB))
if service == Service.WORKER:
    raise ValueError(
        "The worker role runs python manage.py run_dbos_worker, not Gunicorn"
    )
configure(service)

factory = "asgi" if service == Service.GATEWAY else "wsgi"
wsgi_app = f"secondshift.config.app:{factory}('{service}')"
worker_class = "uvicorn_worker.UvicornWorker" if service == Service.GATEWAY else "sync"
bind = settings.HTTP_BIND
workers = settings.HTTP_PROCESSES
timeout = settings.HTTP_TIMEOUT_SECONDS
graceful_timeout = settings.HTTP_GRACEFUL_TIMEOUT_SECONDS
loglevel = settings.HTTP_LOG_LEVEL
accesslog = settings.HTTP_ACCESS_LOG
# Services may share a data directory. Never compete for a shared control socket.
control_socket_disable = True
