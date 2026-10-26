import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from secondshift.config.app import select_settings
from secondshift.config.constants import (
    ENVIRONMENT_VAR,
    SETTINGS_MODULE_VAR,
    Environment,
)
from secondshift.config.first_run import write_generated_env
from secondshift.core.tests.test_settings import load_settings

COMMON = {
    "SECONDSHIFT_SECRET_KEY": "test-server-signing-key",
    "SECONDSHIFT_ENCRYPTION_KEY": "x2Ce3x8qg2ZDiZ0T3W2WXZkCw5rN0p8D2kMlcV1Wq3E=",
    "SECONDSHIFT_DATABASE_URL": "sqlite:///:memory:",
}
WEB = {
    **COMMON,
    "SECONDSHIFT_PUBLIC_URL": "https://app.example",
    "SECONDSHIFT_RUNNER_RUNTIME": "runsc",
}
WORKER = {
    **WEB,
    "SECONDSHIFT_RUNNER_SERVICE_URL": "http://runner:8002",
    "SECONDSHIFT_MODEL_GATEWAY_URL": "http://gateway:8001",
    "SECONDSHIFT_RUNNER_SERVICE_TOKEN": "worker-runner-test-token-at-least-32-characters",
}


@pytest.mark.parametrize(
    ("role", "values"), [("web", WEB), ("gateway", COMMON), ("worker", WORKER)]
)
def test_production_roles_start_with_only_their_settings(
    tmp_path: Path, role: str, values: dict[str, str]
) -> None:
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        SECONDSHIFT_ENV="production",
        SECONDSHIFT_SERVICE=role,
        **values,
    )
    assert result.returncode == 0, result.stderr


def test_web_derives_host_and_csrf_origin_from_public_url(tmp_path: Path) -> None:
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        script="print(s.ALLOWED_HOSTS); print(s.CSRF_TRUSTED_ORIGINS)",
        SECONDSHIFT_ENV="production",
        SECONDSHIFT_SERVICE="web",
        **WEB,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["['app.example']", "['https://app.example']"]


def test_worker_requires_internal_endpoints_and_long_token_without_leaking_values(
    tmp_path: Path,
) -> None:
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        SECONDSHIFT_ENV="production",
        SECONDSHIFT_SERVICE="worker",
        **WEB,
    )
    assert result.returncode != 0
    for name in (
        "SECONDSHIFT_RUNNER_SERVICE_URL",
        "SECONDSHIFT_MODEL_GATEWAY_URL",
        "SECONDSHIFT_RUNNER_SERVICE_TOKEN",
    ):
        assert name in result.stderr
    assert COMMON["SECONDSHIFT_SECRET_KEY"] not in result.stderr
    weak = {**WORKER, "SECONDSHIFT_RUNNER_SERVICE_TOKEN": "short-private-value"}
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        SECONDSHIFT_ENV="production",
        SECONDSHIFT_SERVICE="worker",
        **weak,
    )
    assert result.returncode != 0
    assert "at least 32 characters" in result.stderr
    assert weak["SECONDSHIFT_RUNNER_SERVICE_TOKEN"] not in result.stderr


def test_runner_accepts_prefixed_settings_without_control_plane_keys(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runner.sqlite3"
    result = load_settings(
        "secondshift.config.settings.runner",
        tmp_path,
        script="print(s.ALLOWED_HOSTS); print(s.DATABASES['default']['NAME']); print(hasattr(s, 'FIELD_ENCRYPTION_KEY'))",
        SECONDSHIFT_RUNNER_DATABASE_URL=f"sqlite:///{database}",
        SECONDSHIFT_RUNNER_ALLOWED_HOSTS="private-runner",
        SECONDSHIFT_RUNNER_SERVICE_TOKEN="t" * 32,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["['private-runner']", str(database), "False"]


@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "https://app.example/path",
        "https://user:secret@app.example",
        "https://app.example?token=secret",
        "https://app.example:99999",
        "https://[broken",
    ],
)
def test_public_url_is_an_origin_and_errors_do_not_reveal_its_value(
    tmp_path: Path, url: str
) -> None:
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        SECONDSHIFT_ENV="production",
        SECONDSHIFT_SERVICE="web",
        **{**WEB, "SECONDSHIFT_PUBLIC_URL": url},
    )
    assert result.returncode != 0
    assert "SECONDSHIFT_PUBLIC_URL must be" in result.stderr
    assert url not in result.stderr


def test_worker_concurrency_is_a_positive_job_count(tmp_path: Path) -> None:
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        SECONDSHIFT_ENV="production",
        SECONDSHIFT_SERVICE="worker",
        SECONDSHIFT_RUN_CONCURRENCY="0",
        **WORKER,
    )
    assert result.returncode != 0
    assert "SECONDSHIFT_RUN_CONCURRENCY must be positive" in result.stderr


def test_old_secret_names_are_not_read(tmp_path: Path) -> None:
    old = {
        "DJANGO_SECRET_KEY": "old-signing-value",
        "FIELD_ENCRYPTION_KEY": COMMON["SECONDSHIFT_ENCRYPTION_KEY"],
        "DATABASE_URL": "sqlite:///:memory:",
        "FRONTEND_URL": "https://old.example",
    }
    result = load_settings(
        "secondshift.config.settings.production",
        tmp_path,
        SECONDSHIFT_ENV="production",
        SECONDSHIFT_RUNNER_RUNTIME="runsc",
        **old,
    )
    assert result.returncode != 0
    for name in (
        "SECONDSHIFT_SECRET_KEY",
        "SECONDSHIFT_ENCRYPTION_KEY",
        "SECONDSHIFT_DATABASE_URL",
        "SECONDSHIFT_PUBLIC_URL",
    ):
        assert name in result.stderr
    assert old["DJANGO_SECRET_KEY"] not in result.stderr


def test_old_environment_selector_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.delenv(ENVIRONMENT_VAR, raising=False)
    monkeypatch.delenv(SETTINGS_MODULE_VAR, raising=False)
    monkeypatch.setenv("DJANGO_ENV", "production")
    select_settings(Environment.LOCAL)
    assert os.environ[ENVIRONMENT_VAR] == "local"


def test_generated_keyfile_uses_only_the_new_key_names(tmp_path: Path) -> None:

    write_generated_env(tmp_path)
    content = (tmp_path / ".env").read_text()
    assert "SECONDSHIFT_SECRET_KEY=" in content
    assert "SECONDSHIFT_ENCRYPTION_KEY=" in content
    assert "DJANGO_SECRET_KEY=" not in content
    assert "\nFIELD_ENCRYPTION_KEY=" not in content


def test_generated_legacy_keys_migrate_without_losing_credentials(
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    encrypted = Fernet(key).encrypt(b"saved-connection-credential")
    path = tmp_path / ".env"
    path.write_text(
        "# Existing installation\n"
        "DJANGO_SECRET_KEY=original-signing-key\n"
        f"FIELD_ENCRYPTION_KEY={key.decode()}\n"
        "SECONDSHIFT_RUNNER_SERVICE_TOKEN=original-runner-token\n"
    )
    runner = tmp_path / "runner.env"
    runner.write_text("SECONDSHIFT_RUNNER_SERVICE_TOKEN=original-runner-token\n")
    runner_before = runner.read_bytes()

    write_generated_env(tmp_path)
    migrated = path.read_bytes()
    values = dict(
        line.split("=", 1) for line in path.read_text().splitlines() if "=" in line
    )
    assert values["SECONDSHIFT_SECRET_KEY"] == "original-signing-key"
    assert Fernet(values["SECONDSHIFT_ENCRYPTION_KEY"].encode()).decrypt(encrypted) == (
        b"saved-connection-credential"
    )
    assert "DJANGO_SECRET_KEY" not in values
    assert "FIELD_ENCRYPTION_KEY" not in values
    assert runner.read_bytes() == runner_before
    assert path.stat().st_mode & 0o777 == 0o600
    write_generated_env(tmp_path)
    assert path.read_bytes() == migrated


def test_generated_key_migration_preserves_explicit_current_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    original = (
        "DJANGO_SECRET_KEY=old-signing-key\n"
        "SECONDSHIFT_SECRET_KEY=current-signing-key\n"
        "FIELD_ENCRYPTION_KEY=old-encryption-key\n"
        "SECONDSHIFT_ENCRYPTION_KEY=current-encryption-key\n"
    )
    path.write_text(original)
    write_generated_env(tmp_path)
    assert path.read_text() == original


def test_dev_boots_with_legacy_generated_keys(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    (tmp_path / ".env").write_text(
        "DJANGO_SECRET_KEY=legacy-signing-key\n"
        f"FIELD_ENCRYPTION_KEY={key}\n"
        "SECONDSHIFT_RUNNER_SERVICE_TOKEN=legacy-token-at-least-thirty-two-characters\n"
    )
    result = load_settings(
        "secondshift.config.settings.dev",
        tmp_path,
        SECONDSHIFT_ENV="dev",
        script="import django; django.setup(); print('ready')",
        DJANGO_SETTINGS_MODULE="secondshift.config.settings.dev",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ready"
