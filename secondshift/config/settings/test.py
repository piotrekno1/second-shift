# ruff: noqa: F403, F405
import os
import tempfile

from secondshift.config.constants import ENVIRONMENT_VAR, Environment

# Tests use production defaults, whatever SECONDSHIFT_ENV the shell exports.
os.environ[ENVIRONMENT_VAR] = Environment.TEST
# Files go to a throwaway folder, and ~/.secondshift/.env is never read.
os.environ["SECONDSHIFT_DATA_DIR"] = tempfile.mkdtemp(prefix="secondshift-test-")

from secondshift.config.settings.base import *

INSTALLED_APPS = [*INSTALLED_APPS, "secondshift.runner"]
SECRET_KEY = "test-secret-key"
FIELD_ENCRYPTION_KEY = "x2Ce3x8qg2ZDiZ0T3W2WXZkCw5rN0p8D2kMlcV1Wq3E="
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DBOS_SYSTEM_DATABASE_URL = "sqlite:///:memory:"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SIGNUP_ENABLED = False
# Tests run without collectstatic, so they cannot use the manifest storage.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Tests install every first-party app in one Django process. Deployment settings
# remain separate and are checked independently.
RUNNER_SERVICE_TOKEN = "test-runner-service-token-value"
RUNNER_REPOSITORY_ROOT = BASE_DIR
RUNNER_WORK_ROOT = BASE_DIR / ".secondshift-runner-test"
RUNNER_COMMAND_TIMEOUT_SECONDS = 120
RUNNER_COMMAND_OUTPUT_MAX_BYTES = 1024 * 1024
RUNNER_MEMORY = "2g"
RUNNER_CPUS = "2"
RUNNER_PIDS_LIMIT = 256
RUNNER_SANDBOX_UID = os.getuid()
RUNNER_SANDBOX_GID = os.getgid()

# Internal services in tests have explicit addresses, independent of the shell.
RUNNER_SERVICE_URL = "http://127.0.0.1:8002"
MODEL_GATEWAY_URL = "http://127.0.0.1:8001"
