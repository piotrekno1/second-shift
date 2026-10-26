"""HTTP server settings shared by application and runner processes."""

import os

from environ import Env

from secondshift.config.constants import SERVICE_VAR, GunicornEnv, Service

env = Env()
service = Service(os.environ.get(SERVICE_VAR, Service.WEB))

if service == Service.GATEWAY:
    default_bind = "0.0.0.0:8001"
    default_processes = 2
    default_graceful_timeout = 600
elif service == Service.RUNNER:
    default_bind = "0.0.0.0:8002"
    default_processes = 1
    default_graceful_timeout = 30
else:
    default_bind = "0.0.0.0:8000"
    default_processes = 2
    default_graceful_timeout = 30

HTTP_BIND = env.str(GunicornEnv.BIND, default=default_bind)
HTTP_PROCESSES = env.int(GunicornEnv.WORKERS, default=default_processes)
HTTP_TIMEOUT_SECONDS = env.int(GunicornEnv.TIMEOUT, default=30)
HTTP_GRACEFUL_TIMEOUT_SECONDS = env.int(
    GunicornEnv.GRACEFUL_TIMEOUT, default=default_graceful_timeout
)
HTTP_LOG_LEVEL = env.str(GunicornEnv.LOG_LEVEL, default="info").lower()
HTTP_ACCESS_LOG = env.str(GunicornEnv.ACCESS_LOG, default="-") or None

if HTTP_PROCESSES < 1 or HTTP_TIMEOUT_SECONDS < 1 or HTTP_GRACEFUL_TIMEOUT_SECONDS < 1:
    raise ValueError("Gunicorn worker count and timeouts must be positive")
