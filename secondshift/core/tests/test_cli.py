from importlib.metadata import version

import pytest

from secondshift import cli


@pytest.mark.parametrize("argument", ["--version", "version"])
def test_version_names_secondshift_not_django(
    argument: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["secondshift", argument])

    cli.main()

    assert capsys.readouterr().out == version("secondshift") + "\n"
