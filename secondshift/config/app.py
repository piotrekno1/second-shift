"""Server entry points for the web app, the model gateway and the runner.

Start an HTTP role with SECONDSHIFT_SERVICE set to web, gateway or runner:

    gunicorn --config python:secondshift.config.gunicorn
"""

import os

from django.core.asgi import get_asgi_application
from django.core.handlers.asgi import ASGIHandler
from django.core.handlers.wsgi import WSGIHandler
from django.core.wsgi import get_wsgi_application

from secondshift.config.constants import (
    ENVIRONMENT_VAR,
    RUNNER_SETTINGS,
    SERVICE_VAR,
    SETTINGS_MODULE_VAR,
    Environment,
    Service,
    settings_module,
)


def select_settings(default: Environment) -> None:
    """Use SECONDSHIFT_ENV, or the default, to pick the settings module."""
    environment = Environment(os.environ.setdefault(ENVIRONMENT_VAR, default))
    os.environ.setdefault(SETTINGS_MODULE_VAR, settings_module(environment))


def configure(service: Service) -> None:
    os.environ[SERVICE_VAR] = service
    if service == Service.RUNNER:
        # Always its own settings, so it never loads the control plane's keys.
        os.environ[SETTINGS_MODULE_VAR] = RUNNER_SETTINGS
    else:
        select_settings(default=Environment.PRODUCTION)


def started(service: Service) -> None:
    if service == Service.RUNNER:
        # Imported here because it needs settings, which are chosen above.
        from secondshift.runner.services import get_runner_service  # noqa: PLC0415

        get_runner_service().verify_configuration()


def wsgi(name: str = Service.WEB) -> WSGIHandler:
    service = Service(name)
    configure(service)
    application = get_wsgi_application()
    started(service)
    return application


def asgi(name: str = Service.WEB) -> ASGIHandler:
    service = Service(name)
    configure(service)
    application = get_asgi_application()
    started(service)
    return application
