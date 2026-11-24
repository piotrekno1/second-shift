from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command

from secondshift.runner.agent_credentials import provisioning_public_key
from secondshift.runner_protocol.credentials import key_fingerprint


def test_runner_key_prints_the_fingerprint_the_control_plane_pins(
    settings: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.AGENT_AUTH_ROOT = tmp_path / "agent-auth"

    call_command("runner_key")

    printed = capsys.readouterr().out.strip()
    assert printed == key_fingerprint(provisioning_public_key())
    assert printed.startswith("SHA256:")
