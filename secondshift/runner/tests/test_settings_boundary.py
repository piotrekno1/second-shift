import os
import subprocess
import sys
from pathlib import Path

import pytest


def load_runner_settings(
    tmp_path: Path, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "SYSTEMROOT"}
    }
    environment.update(
        {
            "SECONDSHIFT_RUNNER_SERVICE_TOKEN": "t" * 32,
            "SECONDSHIFT_RUNNER_IMAGE": "secondshift-runner:test",
            "SECONDSHIFT_RUNNER_REPOSITORY_ROOT": str(tmp_path),
            "SECONDSHIFT_RUNNER_WORK_ROOT": str(tmp_path / "work"),
            "SECONDSHIFT_DATA_DIR": str(tmp_path / "data"),
            **(extra or {}),
        }
    )
    script = (
        "import secondshift.config.settings.runner as s; "
        "print(','.join(s.INSTALLED_APPS)); "
        "print(hasattr(s, 'FIELD_ENCRYPTION_KEY'))"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_deployed_runner_settings_keep_the_app_boundary(tmp_path: Path) -> None:
    result = load_runner_settings(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "django.contrib.contenttypes,secondshift.runner",
        "False",
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root may hand workspaces to anyone")
def test_runner_refuses_another_sandbox_user_without_root(tmp_path: Path) -> None:
    other = str(os.geteuid() + 1)
    result = load_runner_settings(
        tmp_path,
        {
            "SECONDSHIFT_RUNNER_SANDBOX_UID": other,
            "SECONDSHIFT_RUNNER_SANDBOX_GID": str(os.getegid()),
        },
    )

    assert result.returncode != 0
    assert "needs a runner running as root" in result.stderr


def test_runner_accepts_its_own_user_as_the_sandbox_user(tmp_path: Path) -> None:
    result = load_runner_settings(
        tmp_path,
        {
            "SECONDSHIFT_RUNNER_SANDBOX_UID": str(os.geteuid()),
            "SECONDSHIFT_RUNNER_SANDBOX_GID": str(os.getegid()),
        },
    )

    assert result.returncode == 0, result.stderr


def test_runner_reads_only_its_token_from_the_data_folder(tmp_path: Path) -> None:
    (tmp_path / "runner.env").write_text("SECONDSHIFT_RUNNER_SERVICE_TOKEN=" + "r" * 40)
    (tmp_path / ".env").write_text("FIELD_ENCRYPTION_KEY=must-not-be-read")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "SYSTEMROOT"}
    }
    environment["SECONDSHIFT_DATA_DIR"] = str(tmp_path)
    script = (
        "import os, secondshift.config.settings.runner as s; "
        "print(s.RUNNER_SERVICE_TOKEN); print(s.RUNNER_REPOSITORY_ROOT); "
        "print('FIELD_ENCRYPTION_KEY' in os.environ)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "r" * 40,
        str(tmp_path / "runner" / "repositories"),
        "False",
    ]
