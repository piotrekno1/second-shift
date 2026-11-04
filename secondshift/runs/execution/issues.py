"""Let the agent propose GitHub issues for SecondShift to open after the run.

The agent never holds a GitHub write token. It only records proposals here.
After a successful run, SecondShift opens them with a token for this one
repository, if an owner allows issues there.
"""

from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import ModelRetry, RunContext, Tool
from pydantic_ai.tools import ToolDefinition

from secondshift.github.issues import MAX_TITLE_CHARS, same_title
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps
from secondshift.runs.models import RunIssue

TOOL_NAME = "propose_issue"
# Long enough for steps to reproduce and a few log lines.
MAX_BODY_CHARS = 20_000

TitleArgument = Annotated[str, Field(min_length=1, max_length=MAX_TITLE_CHARS)]
BodyArgument = Annotated[str, Field(max_length=MAX_BODY_CHARS)]


def issues_note(limit: int) -> str:
    """The prompt lines for runs that may propose issues."""
    return (
        f"Call {TOOL_NAME} once for each issue worth filing, at most {limit}. "
        "SecondShift opens them on GitHub after the run, and skips any whose "
        "title matches an open issue. Give each a specific title and a body "
        "someone can act on without this run. List them in your report as "
        "proposed, because they open only after you finish."
    )


def propose_issue(
    ctx: RunContext[WorkspaceToolDeps], title: TitleArgument, body: BodyArgument
) -> dict[str, Any]:
    """Propose a GitHub issue for this repository. It opens after the run."""
    if not ctx.tool_call_id:
        raise RuntimeError("The model tool call has no stable identifier.")
    title = " ".join(title.split())
    if not title:
        raise ModelRetry("Give the issue a title.")
    existing = RunIssue.objects.filter(run_id=ctx.deps.run_id)
    # A replayed call finds the proposal it already made.
    if existing.filter(call_id=ctx.tool_call_id).exists():
        return proposed(existing.count(), ctx.deps.issue_limit)
    if any(
        same_title(item, title) for item in existing.values_list("title", flat=True)
    ):
        return {
            "proposed": False,
            "note": "An issue with this title is already proposed in this run.",
        }
    if existing.count() >= ctx.deps.issue_limit:
        return {
            "proposed": False,
            "note": (
                f"This run has proposed the most issues it may, "
                f"{ctx.deps.issue_limit}. Put the rest in the report."
            ),
        }
    RunIssue.objects.create(
        run_id=ctx.deps.run_id,
        call_id=ctx.tool_call_id,
        title=title,
        body=body.strip(),
    )
    return proposed(existing.count(), ctx.deps.issue_limit)


def proposed(count: int, limit: int) -> dict[str, Any]:
    return {
        "proposed": True,
        "note": (
            f"Proposed {count} of at most {limit}. SecondShift opens it after the run."
        ),
    }


async def only_with_issues(
    ctx: RunContext[WorkspaceToolDeps], definition: ToolDefinition
) -> ToolDefinition | None:
    return definition if ctx.deps.issue_limit else None


def issue_tool() -> Tool[WorkspaceToolDeps]:
    return Tool(propose_issue, name=TOOL_NAME, prepare=only_with_issues)


def issue_limit(plan: dict[str, Any]) -> int:
    """How many issues the plan lets the agent propose. Zero means none."""
    issues = plan.get("issues") or {}
    return int(issues.get("max", 0))
