import ast
import re
from itertools import product
from pathlib import Path

import pytest

import secondshift
from secondshift.runs.models import Run
from secondshift.runs.orchestration import workflows
from secondshift.runs.tests.test_reconciliation import create_run
from secondshift.runs.transitions import (
    TransitionRefused,
    can_move,
    finish_run,
    start_run,
)

ALLOWED = {
    (Run.Status.QUEUED, Run.Status.RUNNING),
    (Run.Status.QUEUED, Run.Status.FAILED),
    (Run.Status.QUEUED, Run.Status.CANCELED),
    (Run.Status.RUNNING, Run.Status.SUCCEEDED),
    (Run.Status.RUNNING, Run.Status.FAILED),
    (Run.Status.RUNNING, Run.Status.CANCELED),
}


@pytest.mark.parametrize(("current", "new"), list(product(Run.Status, Run.Status)))
def test_only_the_lifecycle_moves_are_allowed(current: str, new: str) -> None:
    assert can_move(current, new) is ((current, new) in ALLOWED)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "final", [Run.Status.SUCCEEDED, Run.Status.FAILED, Run.Status.CANCELED]
)
def test_a_finished_run_never_changes(final: str) -> None:
    run = create_run(status=final, workflow_id="wf-1")

    assert finish_run(run.pk, Run.Status.FAILED, "Too late.") is None
    assert start_run(run.pk, "wf-2") is False
    run.refresh_from_db()
    assert run.status == final
    assert run.summary == ""


@pytest.mark.django_db
def test_a_queued_run_cannot_succeed_without_running() -> None:
    run = create_run(status=Run.Status.QUEUED, workflow_id="wf-1")

    with pytest.raises(TransitionRefused):
        finish_run(run.pk, Run.Status.SUCCEEDED, "Skipped ahead.")
    with pytest.raises(TransitionRefused):
        finish_run(run.pk, Run.Status.RUNNING, "Not a final status.")


@pytest.mark.django_db
def test_finishing_sets_the_summary_time_and_fields() -> None:
    run = create_run(status=Run.Status.QUEUED, workflow_id="wf-1")
    assert start_run(run.pk, "wf-1") is True

    finished = finish_run(run.pk, Run.Status.FAILED, "Broke.", fields={"exit_code": 2})

    assert finished is not None
    run.refresh_from_db()
    assert (run.status, run.summary, run.exit_code) == (Run.Status.FAILED, "Broke.", 2)
    assert run.started_at is not None and run.finished_at is not None


@pytest.mark.django_db
def test_a_late_result_does_not_overwrite_a_canceled_run() -> None:
    run = create_run(status=Run.Status.QUEUED, workflow_id="wf-1")
    start_run(run.pk, "wf-1")
    finish_run(run.pk, Run.Status.CANCELED, "Canceled.")

    workflows.finish_run_step.__wrapped__(
        run.pk, {"succeeded": True, "exit_code": 0, "summary": "Done."}
    )

    run.refresh_from_db()
    assert run.status == Run.Status.CANCELED
    assert run.summary == "Canceled."
    assert not run.events.exists()


RUN_STATUS = re.compile(r"\bRun\.Status\.")
ALLOWED_FILES = {"transitions.py"}


def status_writes(tree: ast.AST) -> list[int]:
    """Lines that set a run's status outside the transitions module."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "status"
                    and (
                        RUN_STATUS.search(ast.unparse(node.value))
                        or ast.unparse(target.value) == "run"
                    )
                ):
                    lines.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ):
            for keyword in node.keywords:
                if keyword.arg == "status" and RUN_STATUS.search(
                    ast.unparse(keyword.value)
                ):
                    lines.append(node.lineno)
    return lines


def test_only_the_transitions_module_changes_a_run_status() -> None:
    package = Path(secondshift.__file__).parent
    found = []
    for path in package.rglob("*.py"):
        parts = set(path.relative_to(package).parts)
        if parts & {"tests", "migrations", "benchmarks"} or path.name in ALLOWED_FILES:
            continue
        for line in status_writes(ast.parse(path.read_text())):
            found.append(f"{path.relative_to(package)}:{line}")
    assert found == []
