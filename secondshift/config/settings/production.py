# ruff: noqa: F403, F405
from secondshift.config.constants import Environment, SandboxRuntime, Service
from secondshift.config.settings.base import *

if ENVIRONMENT != Environment.PRODUCTION:
    raise ImproperlyConfigured(
        "secondshift.config.settings.production requires DJANGO_ENV=production"
    )

# Every problem is reported at once, and the process refuses to start.
errors: list[str] = []


def required(name: str) -> str:
    value = env.str(name, default="")
    if not value:
        errors.append(f"{name} must be set")
    return value


# No development fallbacks for secrets or the database in production.
SECRET_KEY = required("DJANGO_SECRET_KEY")
FIELD_ENCRYPTION_KEY = required("FIELD_ENCRYPTION_KEY")
required("DATABASE_URL")
if DEBUG:
    errors.append("DJANGO_DEBUG must be off")
if not CSRF_TRUSTED_ORIGINS:
    errors.append("CSRF_TRUSTED_ORIGINS must be set")
if ARTIFACT_STORAGE == "s3" and not ARTIFACT_STORAGES["s3"]["OPTIONS"]["bucket_name"]:
    errors.append("SECONDSHIFT_ARTIFACT_BUCKET must be set for s3 storage")
if RUNNER_RUNTIME != SandboxRuntime.GVISOR:
    errors.append("SECONDSHIFT_RUNNER_RUNTIME must be runsc (gVisor)")
required("SECONDSHIFT_RUNNER_SERVICE_URL")
required("SECONDSHIFT_RUNNER_SERVICE_TOKEN")
required("SECONDSHIFT_MODEL_GATEWAY_URL")
if errors:
    raise ImproperlyConfigured(
        "Production settings are incomplete:\n- " + "\n- ".join(errors)
    )

# --- HTTPS hardening ---------------------------------------------------------

# Trust X-Forwarded-Proto only from a TLS-terminating proxy that overwrites it
# (kamal-proxy does). The app container must not be reachable any other way.
if env.bool("SECONDSHIFT_BEHIND_TLS_PROXY", default=True):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# The gateway process is reached only by trusted orchestration services.
SECURE_SSL_REDIRECT = SERVICE != Service.GATEWAY
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
