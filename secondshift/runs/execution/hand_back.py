"""Let the agent hand an approved issue back to a person.

The agent only records which issue and why. After a successful run,
SecondShift comments on the issue and swaps its labels with a token for this
one repository, if the issue still has the job's approval label.
"""

from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import ModelRetry, RunContext, Tool
from pydantic_ai.tools import ToolDefinition

from secondshift.jobs.execution_plan import ExecutionPlan
from secondshift.runs.dao import create_hand_back, run_hand_back
from secondshift.runs.execution.workspace_tools import WorkspaceToolDeps

TOOL_NAME = "hand_back_issue"
# Room for findings with file references and a plan of a few steps.
MAX_COMMENT_CHARS = 10_000

NumberArgument = Annotated[int, Field(ge=1)]
CommentArgument = Annotated[str, Field(min_length=1, max_length=MAX_COMMENT_CHARS)]


def approved_issues_note(plan: ExecutionPlan) -> str:
    """The prompt lines for jobs that work on issues a maintainer approved."""
    approved = plan.approved_issues
    if approved is None:
        return ""
    added = f" and adds {approved.hand_back}" if approved.hand_back else ""
    return (
        f"A maintainer approves an issue for this job with the label "
        f"{approved.label}. Work only on open issues that have it. To hand an "
        f"approved issue back to a person instead, call {TOOL_NAME} once with "
        "its number and a comment for the maintainers: why it needs a person, "
        "what you found, and a suggested plan. After the run, SecondShift "
        f"posts the comment, removes {approved.label}{added}. Leave the "
        "workspace unchanged when you hand an issue back."
    )


def hand_back_issue(
    ctx: RunContext[WorkspaceToolDeps],
    number: NumberArgument,
    comment: CommentArgument,
) -> dict[str, Any]:
    """Hand one approved issue back to a person. SecondShift comments on it and
    swaps its labels after the run."""
    if not ctx.tool_call_id:
        raise RuntimeError("The model tool call has no stable identifier.")
    comment = comment.strip()
    if not comment:
        raise ModelRetry("Write a comment that says why the issue needs a person.")
    run_id = ctx.deps.run_id
    existing = run_hand_back(run_id)
    # A replayed call finds the hand-back it already made.
    if existing is not None and existing.call_id == ctx.tool_call_id:
        return handed_back(existing.number)
    if existing is not None:
        return {
            "handed_back": False,
            "note": f"This run already handed back #{existing.number}. "
            "A run hands back one issue.",
        }
    create_hand_back(run_id, ctx.tool_call_id, number, comment)
    return handed_back(number)


def handed_back(number: int) -> dict[str, Any]:
    return {
        "handed_back": True,
        "note": f"SecondShift comments on #{number} and swaps its labels after "
        "the run. Say so in your report.",
    }


async def only_with_hand_back(
    ctx: RunContext[WorkspaceToolDeps], definition: ToolDefinition
) -> ToolDefinition | None:
    return definition if ctx.deps.hand_back else None


def hand_back_tool() -> Tool[WorkspaceToolDeps]:
    return Tool(hand_back_issue, name=TOOL_NAME, prepare=only_with_hand_back)
