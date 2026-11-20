from pathlib import Path

from secondshift.connections.mcp.github import GITHUB_MCP_TOOLS
from secondshift.jobs.manifest import load_manifest

EXAMPLE = Path("examples/issue-fixer")


def test_the_issue_fixer_example_reads_issues_and_opens_drafts() -> None:
    job = load_manifest(EXAMPLE / ".secondshift.yaml").jobs["issue-fixer"]

    assert job.open_pull_request
    # An issue that needs a person goes back to the maintainers.
    assert job.approved_issues is not None
    assert job.approved_issues.label == "fix-approved"
    # Earlier reports name the issues already handled.
    assert job.history > 0
    [github] = job.requires.mcps
    assert github.binding == "github"
    assert set(github.tools) <= set(GITHUB_MCP_TOOLS)
    # A closed draft's comments and reviews say what a new attempt must change.
    assert {"list_issues", "issue_read", "pull_request_read"} <= set(github.tools)
    assert (EXAMPLE / job.skill).is_file()


def test_the_root_manifest_runs_the_example_skill() -> None:
    job = load_manifest(Path(".secondshift.yaml")).jobs["issue-fixer"]

    assert job.skill == f"{EXAMPLE}/.agents/skills/issue-fixer/SKILL.md"
    assert job.open_pull_request
    # A fix may touch the dashboard, and the default image has no Node.
    assert job.environment == "python_node"
    assert any("npm ci" in command for command in job.setup)
    assert Path(job.skill).is_file()
