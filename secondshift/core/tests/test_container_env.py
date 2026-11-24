from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from secondshift.config.container_env import (
    generate_environment,
    generate_runner_environment,
)


def read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


def test_role_files_are_private_and_have_only_their_required_settings(
    tmp_path: Path,
) -> None:
    generate_environment("app.example", tmp_path)
    web = read_env(tmp_path / "web.env")
    gateway = read_env(tmp_path / "gateway.env")
    worker = read_env(tmp_path / "worker.env")
    runner = read_env(tmp_path / "runner.env")
    for role in ("web", "gateway", "worker", "runner"):
        assert (tmp_path / f"{role}.env").stat().st_mode & 0o777 == 0o600
        assert read_env(tmp_path / f"{role}.env")["SECONDSHIFT_SERVICE"] == role
    for name in (
        "SECONDSHIFT_SECRET_KEY",
        "SECONDSHIFT_ENCRYPTION_KEY",
        "SECONDSHIFT_DATABASE_URL",
    ):
        assert web[name] == gateway[name] == worker[name]
        assert name not in runner
    Fernet(web["SECONDSHIFT_ENCRYPTION_KEY"].encode())
    assert (
        web["SECONDSHIFT_PUBLIC_URL"]
        == worker["SECONDSHIFT_PUBLIC_URL"]
        == "https://app.example"
    )
    assert "SECONDSHIFT_PUBLIC_URL" not in gateway
    # The gateway's agent broker runs workspace tools for subscription runs.
    assert (
        web["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
        == gateway["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
        == worker["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
    )
    assert (
        web["SECONDSHIFT_RUNNER_SERVICE_URL"]
        == gateway["SECONDSHIFT_RUNNER_SERVICE_URL"]
        == worker["SECONDSHIFT_RUNNER_SERVICE_URL"]
    )
    assert (
        worker["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
        == runner["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
    )
    assert "SECONDSHIFT_MODEL_GATEWAY_URL" not in gateway
    assert runner["SECONDSHIFT_RUNNER_WORK_ROOT"] == str(tmp_path / "runner")
    # Only the runner names sandbox images.
    assert runner["SECONDSHIFT_SANDBOX_DEFAULT"] == "python"
    assert runner["SECONDSHIFT_SANDBOX_PYTHON"].startswith("python:3.13-slim@sha256:")
    assert "SECONDSHIFT_SANDBOX_PYTHON_NODE" in runner
    for values in (web, gateway, worker):
        assert not any(name.startswith("SECONDSHIFT_SANDBOX_") for name in values)


def test_a_runner_on_another_host_gets_only_its_own_file(tmp_path: Path) -> None:
    generate_runner_environment("Runner-B.example", tmp_path)
    assert [path.name for path in tmp_path.glob("*.env")] == ["runner.env"]
    runner = read_env(tmp_path / "runner.env")
    assert (tmp_path / "runner.env").stat().st_mode & 0o777 == 0o600
    assert runner["SECONDSHIFT_SERVICE"] == "runner"
    # Caddy passes on the runner's own hostname.
    assert runner["SECONDSHIFT_RUNNER_ALLOWED_HOSTS"] == "runner-b.example"
    assert len(runner["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]) >= 32
    assert runner["SECONDSHIFT_RUNNER_RUNTIME"] == "runsc"
    assert "SECONDSHIFT_SANDBOX_PYTHON" in runner
    assert "SECONDSHIFT_ENCRYPTION_KEY" not in runner


@pytest.mark.parametrize("existing", ["web.env", "runner.env", ".env"])
def test_generator_does_not_rekey_existing_installations(
    tmp_path: Path, existing: str
) -> None:
    (tmp_path / existing).write_text("keep-existing-keys")
    with pytest.raises(FileExistsError, match="reuse"):
        generate_environment("app.example", tmp_path)
    assert (tmp_path / existing).read_text() == "keep-existing-keys"
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize(
    "hostname",
    [
        "https://app.example",
        "app.example/path",
        "app.example:443",
        "user:secret@app.example",
        "app.example\nSECONDSHIFT_DEBUG=true",
    ],
)
def test_hostname_cannot_inject_urls_credentials_or_env_variables(
    tmp_path: Path, hostname: str
) -> None:
    with pytest.raises(ValueError, match="DNS hostname"):
        generate_environment(hostname, tmp_path)
    assert not list(tmp_path.iterdir())
