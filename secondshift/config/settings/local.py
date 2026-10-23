# ruff: noqa: F403, F405
"""`secondshift up`: one person running SecondShift on their own machine."""

from secondshift.config.constants import Environment
from secondshift.config.settings.base import *

if ENVIRONMENT != Environment.LOCAL:
    raise ImproperlyConfigured(
        "secondshift.config.settings.local requires DJANGO_ENV=local"
    )

# Static files and the dashboard are served straight from the installed
# package, so there is no collectstatic step.
STATIC_ROOT = None
WHITENOISE_USE_FINDERS = True
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
