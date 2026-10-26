from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from secondshift.config.container_env import generate_environment


def read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


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
    assert (
        web["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
        == worker["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
    )
    assert (
        web["SECONDSHIFT_RUNNER_SERVICE_URL"]
        == worker["SECONDSHIFT_RUNNER_SERVICE_URL"]
    )
    assert "SECONDSHIFT_RUNNER_SERVICE_TOKEN" not in gateway
    assert (
        worker["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
        == runner["SECONDSHIFT_RUNNER_SERVICE_TOKEN"]
    )
    assert "SECONDSHIFT_MODEL_GATEWAY_URL" not in gateway
    assert runner["SECONDSHIFT_RUNNER_WORK_ROOT"] == str(tmp_path / "runner")


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
