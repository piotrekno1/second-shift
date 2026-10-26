# ruff: noqa: F403, F405
from secondshift.config.constants import AppEnv, Environment, SandboxRuntime, Service
from secondshift.config.settings.base import *

if ENVIRONMENT != Environment.PRODUCTION:
    raise ImproperlyConfigured(
        "secondshift.config.settings.production requires SECONDSHIFT_ENV=production"
    )

# Every problem is reported at once, and the process refuses to start.
errors: list[str] = []


def required(name: str) -> str:
    value = env.str(name, default="")
    if not value:
        errors.append(f"{name} must be set")
    return value


# No development fallbacks for secrets or the database in production.
SECRET_KEY = required(AppEnv.SECRET_KEY)
FIELD_ENCRYPTION_KEY = required(AppEnv.ENCRYPTION_KEY)
required(AppEnv.DATABASE_URL)
if DEBUG:
    errors.append(f"{AppEnv.DEBUG} must be off")
if SERVICE == Service.WEB and not CSRF_TRUSTED_ORIGINS:
    errors.append(f"{AppEnv.CSRF_TRUSTED_ORIGINS} must be set")
if SERVICE in {Service.WEB, Service.WORKER}:
    try:
        public_origin = urlsplit(FRONTEND_URL)
        valid_origin = (
            public_origin.scheme == "https"
            and bool(public_origin.hostname)
            and not public_origin.username
            and not public_origin.password
            and public_origin.path in {"", "/"}
            and not public_origin.query
            and not public_origin.fragment
            and public_origin.port != 0
        )
    except ValueError:
        valid_origin = False
    if not valid_origin:
        errors.append(f"{AppEnv.PUBLIC_URL} must be the public HTTPS address")
    if (
        ARTIFACT_STORAGE == "s3"
        and not ARTIFACT_STORAGES["s3"]["OPTIONS"]["bucket_name"]
    ):
        errors.append("SECONDSHIFT_ARTIFACT_BUCKET must be set for s3 storage")
    if RUNNER_RUNTIME != SandboxRuntime.GVISOR:
        errors.append("SECONDSHIFT_RUNNER_RUNTIME must be runsc (gVisor)")
if SERVICE == Service.WORKER:
    if DBOS_WORKER_CONCURRENCY < 1:
        errors.append(f"{AppEnv.RUN_CONCURRENCY} must be positive")
    required(AppEnv.RUNNER_SERVICE_URL)
    required(AppEnv.MODEL_GATEWAY_URL)
    if len(required(AppEnv.RUNNER_SERVICE_TOKEN)) < 32:
        errors.append(
            "SECONDSHIFT_RUNNER_SERVICE_TOKEN must contain at least 32 characters"
        )
# Partial App configuration must not silently disable production repository sync.
app_configuration = (
    GITHUB_APP_ID,
    GITHUB_APP_SLUG,
    GITHUB_APP_CLIENT_ID,
    GITHUB_APP_CLIENT_SECRET,
    GITHUB_APP_PRIVATE_KEY,
)
if SERVICE in {Service.WEB, Service.WORKER} and any(app_configuration):
    if not all(app_configuration):
        errors.append(
            "All SECONDSHIFT_GITHUB_APP_* settings must be configured together"
        )
    if SERVICE == Service.WEB:
        required(AppEnv.RUNNER_SERVICE_URL)
        if len(required(AppEnv.RUNNER_SERVICE_TOKEN)) < 32:
            errors.append(
                "SECONDSHIFT_RUNNER_SERVICE_TOKEN must contain at least 32 characters"
            )
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
