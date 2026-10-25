import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from secondshift.config import app
from secondshift.config.settings.base import sqlalchemy_url

CHECKOUT_ENV = Path(__file__).resolve().parents[3] / ".env"

PRODUCTION_ENV = {
    "DJANGO_SECRET_KEY": "test-secret",
    "FIELD_ENCRYPTION_KEY": "x2Ce3x8qg2ZDiZ0T3W2WXZkCw5rN0p8D2kMlcV1Wq3E=",
    "DATABASE_URL": "postgres://user:pass@db:5432/app",
    "CSRF_TRUSTED_ORIGINS": "https://app.example",
    "FRONTEND_URL": "https://app.example",
    "SECONDSHIFT_RUNNER_RUNTIME": "runsc",
    "SECONDSHIFT_RUNNER_SERVICE_URL": "http://runner:8002",
    "SECONDSHIFT_RUNNER_SERVICE_TOKEN": "runner-service-token",
    "SECONDSHIFT_MODEL_GATEWAY_URL": "http://gateway:8001",
}


def load_settings(
    module: str, data_dir: Path, *, script: str = "", **variables: str
) -> subprocess.CompletedProcess[str]:
    # A fresh interpreter, so module-level settings code runs with only these
    # variables (the developer's .env must not mask a missing value).
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "SYSTEMROOT"}
    }
    # The checkout's .env is skipped; the data folder's is read as usual.
    script = (
        "import environ; read = environ.Env.read_env; "
        "environ.Env.read_env = staticmethod("
        f"lambda path, **k: None if str(path) == {str(CHECKOUT_ENV)!r} "
        "else read(path, **k)); "
        f"import {module} as s; "
        + (script or "print(s.DEBUG, s.SIGNUP_ENABLED, s.OUTBOUND_ALLOW_LOCAL)")
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        env={**env, "SECONDSHIFT_DATA_DIR": str(data_dir), **variables},
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_reports_every_missing_value_at_once(tmp_path: Path) -> None:
    result = load_settings(
        "secondshift.config.settings.production", tmp_path, DJANGO_ENV="production"
    )

    assert result.returncode != 0
    for name in (
        "DJANGO_SECRET_KEY",
        "FIELD_ENCRYPTION_KEY",
        "DATABASE_URL",
        "CSRF_TRUSTED_ORIGINS",
        "FRONTEND_URL",
        "SECONDSHIFT_RUNNER_RUNTIME",
        "SECONDSHIFT_RUNNER_SERVICE_URL",
        "SECONDSHIFT_RUNNER_SERVICE_TOKEN",
        "SECONDSHIFT_MODEL_GATEWAY_URL",
    ):
        assert f"{name} must be" in result.stderr


def test_production_requires_an_https_frontend_url(tmp_path: Path) -> None:
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        DJANGO_ENV="production",
        **{**PRODUCTION_ENV, "FRONTEND_URL": "http://app.example"},
    )

    assert result.returncode != 0
    assert "FRONTEND_URL must be the public HTTPS address" in result.stderr


def test_production_loads_with_required_values_and_ignores_dev_flags(
    tmp_path: Path,
) -> None:
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        DJANGO_ENV="production",
        SECONDSHIFT_OUTBOUND_ALLOW_LOCAL="true",
        **PRODUCTION_ENV,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False", "False"]


def test_dev_defaults_apply_only_in_dev(tmp_path: Path) -> None:
    result = load_settings(
        "secondshift.config.settings.dev", tmp_path, DJANGO_ENV="dev"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True", "True"]


@pytest.mark.parametrize(
    ("module", "environment"),
    [
        ("secondshift.config.settings.dev", "production"),
        ("secondshift.config.settings.production", "dev"),
    ],
)
def test_settings_module_must_match_django_env(
    module: str, environment: str, tmp_path: Path
) -> None:
    result = load_settings(module, tmp_path, DJANGO_ENV=environment, **PRODUCTION_ENV)

    assert result.returncode != 0
    assert "requires DJANGO_ENV" in result.stderr


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@db/app", "postgresql+psycopg://u:p@db/app"),
        ("postgresql://u:p@db/app", "postgresql+psycopg://u:p@db/app"),
        ("sqlite:///db.sqlite3", "sqlite:///db.sqlite3"),
    ],
)
def test_dbos_url_names_the_psycopg_driver(url: str, expected: str) -> None:
    assert sqlalchemy_url(url) == expected


KEYS_SCRIPT = (
    "print(s.SECRET_KEY); print(s.FIELD_ENCRYPTION_KEY); "
    "print(s.RUNNER_SERVICE_TOKEN); print(s.DATABASES['default']['NAME'])"
)


def test_dev_generates_keys_once_into_the_data_folder(tmp_path: Path) -> None:
    first = load_settings(
        "secondshift.config.settings.dev",
        tmp_path,
        script=KEYS_SCRIPT,
        DJANGO_ENV="dev",
    )
    second = load_settings(
        "secondshift.config.settings.dev",
        tmp_path,
        script=KEYS_SCRIPT,
        DJANGO_ENV="dev",
    )

    assert first.returncode == 0, first.stderr
    secret, field_key, runner_token, database = first.stdout.split()
    assert second.stdout == first.stdout
    assert len(secret) >= 50 and len(runner_token) >= 50
    Fernet(field_key.encode())
    assert database == str(tmp_path / "secondshift.sqlite3")
    # The runner's file holds only its token, never the encryption key.
    runner_env = (tmp_path / "runner.env").read_text()
    assert runner_env == f"SECONDSHIFT_RUNNER_SERVICE_TOKEN={runner_token}\n"
    for name in (".env", "runner.env"):
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o600


def test_configured_keys_win_over_the_data_folder(tmp_path: Path) -> None:
    result = load_settings(
        "secondshift.config.settings.dev",
        tmp_path,
        script=KEYS_SCRIPT,
        DJANGO_ENV="dev",
        **PRODUCTION_ENV,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[:3] == [
        "test-secret",
        PRODUCTION_ENV["FIELD_ENCRYPTION_KEY"],
        "runner-service-token",
    ]


def test_production_never_generates_keys(tmp_path: Path) -> None:
    load_settings(
        "secondshift.config.settings.production", tmp_path, DJANGO_ENV="production"
    )

    assert list(tmp_path.iterdir()) == []


def test_debug_is_independent_in_dev_and_refused_in_production(
    tmp_path: Path,
) -> None:
    dev = load_settings(
        "secondshift.config.settings.dev",
        tmp_path,
        DJANGO_ENV="dev",
        DJANGO_DEBUG="false",
    )
    production = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        DJANGO_ENV="production",
        DJANGO_DEBUG="true",
        **PRODUCTION_ENV,
    )

    assert dev.returncode == 0, dev.stderr
    assert dev.stdout.split()[0] == "False"
    assert production.returncode != 0
    assert "DJANGO_DEBUG must be off" in production.stderr


def test_sqlite_waits_for_writers_instead_of_failing(tmp_path: Path) -> None:
    script = "print(s.DATABASES['default'].get('OPTIONS'))"
    sqlite = load_settings(
        "secondshift.config.settings.dev", tmp_path, script=script, DJANGO_ENV="dev"
    )
    postgres = load_settings(
        "secondshift.config.settings.dev",
        tmp_path,
        script=script,
        DJANGO_ENV="dev",
        DATABASE_URL="postgres://user:pass@db:5432/app",
    )

    assert "journal_mode=WAL" in sqlite.stdout
    assert "'transaction_mode': 'IMMEDIATE'" in sqlite.stdout
    assert postgres.stdout.strip() == "None"


def test_local_is_a_single_host_install_without_debug(tmp_path: Path) -> None:
    script = (
        "print(s.DEBUG, s.LOCAL_REPOSITORIES_ENABLED, s.OUTBOUND_ALLOW_LOCAL, "
        "s.STATIC_ROOT, s.RUNNER_SERVICE_URL, bool(s.FIELD_ENCRYPTION_KEY), "
        "s.FRONTEND_URL)"
    )
    result = load_settings(
        "secondshift.config.settings.local", tmp_path, script=script, DJANGO_ENV="local"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "False",
        "True",
        "True",
        "None",
        "http://127.0.0.1:8002",
        "True",
        "http://127.0.0.1:8000",
    ]
    assert (tmp_path / ".env").exists()


def test_gateway_service_serves_only_gateway_routes(tmp_path: Path) -> None:
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        script="print(s.ROOT_URLCONF, s.SECURE_SSL_REDIRECT)",
        DJANGO_ENV="production",
        SECONDSHIFT_SERVICE="gateway",
        **PRODUCTION_ENV,
    )

    assert result.stdout.split() == ["secondshift.gateway.urls", "False"]


def test_runner_entry_point_ignores_other_settings_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DJANGO_SETTINGS_MODULE", "secondshift.config.settings.production"
    )
    monkeypatch.setenv("SECONDSHIFT_SERVICE", "web")
    app.configure("runner")

    assert os.environ["DJANGO_SETTINGS_MODULE"] == "secondshift.config.settings.runner"


@pytest.mark.parametrize(
    ("module", "environment", "reads_checkout_env"),
    [
        ("secondshift.config.settings.local", "local", False),
        ("secondshift.config.settings.dev", "dev", True),
    ],
)
def test_local_ignores_the_checkout_env(
    module: str, environment: str, reads_checkout_env: bool, tmp_path: Path
) -> None:
    script = (
        "import environ; paths = []; "
        "environ.Env.read_env = staticmethod("
        "lambda path, **k: paths.append(str(path))); "
        f"import {module}; print({str(CHECKOUT_ENV)!r} in paths)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "SECONDSHIFT_DATA_DIR": str(tmp_path),
            "DJANGO_ENV": environment,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(reads_checkout_env)


EMAIL_SCRIPT = "print(s.EMAIL_BACKEND.rsplit('.', 2)[-2], s.ACCOUNT_EMAIL_VERIFICATION)"


@pytest.mark.parametrize(
    ("module", "environment", "variables", "expected"),
    [
        ("production", "production", PRODUCTION_ENV, ["dummy", "none"]),
        ("local", "local", {}, ["console", "none"]),
        ("dev", "dev", {}, ["console", "optional"]),
        (
            "production",
            "production",
            {**PRODUCTION_ENV, "EMAIL_URL": "smtp://user:pw@mail.example:587"},
            ["smtp", "optional"],
        ),
    ],
)
def test_email_is_sent_and_confirmed_only_with_an_email_url(
    tmp_path: Path,
    module: str,
    environment: str,
    variables: dict[str, str],
    expected: list[str],
) -> None:
    result = load_settings(
        f"secondshift.config.settings.{module}",
        tmp_path,
        script=EMAIL_SCRIPT,
        DJANGO_ENV=environment,
        **variables,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == expected
