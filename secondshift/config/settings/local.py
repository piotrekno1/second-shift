# ruff: noqa: F403, F405
"""`secondshift up`: one person running SecondShift on their own machine."""

from secondshift.config.constants import DEFAULT_HTTP_PORTS, Environment, Service
from secondshift.config.settings.base import *

if ENVIRONMENT != Environment.LOCAL:
    raise ImproperlyConfigured(
        "secondshift.config.settings.local requires SECONDSHIFT_ENV=local"
    )

# `secondshift up` serves the dashboard here unless told otherwise. It sets
# FRONTEND_URL for its processes when --host or --port change it.
FRONTEND_URL = env.str(
    AppEnv.PUBLIC_URL, default=f"http://127.0.0.1:{DEFAULT_HTTP_PORTS[Service.WEB]}"
)

# Static files and the dashboard are served straight from the installed
# package, so there is no collectstatic step.
STATIC_ROOT = None
WHITENOISE_USE_FINDERS = True
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
