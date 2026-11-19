import re
from pathlib import Path

import pytest

from secondshift.runs.orchestration import durable

PACKAGE = Path(__file__).resolve().parents[2]


def test_a_step_drops_broken_connections_before_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        durable, "close_old_connections", lambda: calls.append("checked")
    )

    @durable.fresh_connection
    def step() -> str:
        calls.append("ran")
        return "done"

    assert step() == "done"
    assert calls == ["checked", "ran"]


@pytest.mark.django_db
def test_a_step_inside_a_transaction_keeps_its_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pytest-django runs this test in a transaction. Closing it would break it.
    calls: list[str] = []
    monkeypatch.setattr(
        durable, "close_old_connections", lambda: calls.append("checked")
    )

    durable.refresh_connections()

    assert calls == []


def test_every_dbos_step_and_workflow_checks_its_connection() -> None:
    direct = re.compile(r"^@DBOS\.(step|workflow)\(", re.MULTILINE)
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if "tests" not in path.parts and direct.search(path.read_text())
    ]

    assert offenders == [], "Use durable.step or durable.workflow instead."
