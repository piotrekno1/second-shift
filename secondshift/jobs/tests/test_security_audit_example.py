from pathlib import Path

from secondshift.jobs.manifest import load_manifest

EXAMPLE = Path("examples/security-audit")


def test_the_security_audit_example_keeps_its_ledger_and_verifies_findings() -> None:
    job = load_manifest(EXAMPLE / ".secondshift.yaml").jobs["security-audit"]

    # The ledger and findings come back from the last run.
    assert job.history == 1
    # Fresh agents verify candidates when the run can start them.
    assert job.subagents > 0
    assert job.requires.mcps == [] and job.requires.apis == []
    assert (EXAMPLE / job.skill).is_file()
