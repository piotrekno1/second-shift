# ruff: noqa: F403, F405
from secondshift.config.settings.base import *

if not DEV:
    raise ImproperlyConfigured(
        "secondshift.config.settings.dev requires DJANGO_ENV=dev"
    )

INSTALLED_APPS += ["django_extensions"]
# The Vite dev server calls the API from another origin. Its proxy rewrites
# Host to the backend, so the browser Origin must also be trusted for CSRF.
CORS_ALLOWED_ORIGINS = [FRONTEND_URL]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, FRONTEND_URL]
