"""Submit a repository skill without installing a reusable scheduled job."""

from thin_mcp import ToolContext, ToolExecutionError

from secondshift.config.constants import TaskOrigin
from secondshift.mcp_server.schemas import RunArguments, RunResult
from secondshift.mcp_server.tools_registry import call_request, register_tool
from secondshift.repositories.access import repository_for_request
from secondshift.runs.access import run_for_request
from secondshift.runs.queue import cancel_queued_task
from secondshift.runs.schemas import TaskSubmissionSchema
from secondshift.runs.serialization import serialize_run, serialize_run_detail
from secondshift.runs.services import RunRefused
from secondshift.runs.submissions import SkillSubmission, submit_skill
from secondshift.workspaces.access import Action, require


@register_tool(
    name="submit_task",
    description=(
        "Submit written instructions or a committed repository skill for one execution, without creating a job. "
        "Provide a definition (name, optional skill, logical requires/notify) and input "
        "and an optional branch or full commit revision. Without a skill, input must describe the task. Defaults to the repository's followed "
        "branch and pins the accepted commit. Reuse the same idempotency_key when "
        "retrying the same request. Connections must allow automatic approval. "
        "Tasks queue persistently and execute one at a time per repository. "
        "Set optional not_before to a timezone-aware timestamp to delay execution. "
        "The started field means a new run was accepted, not that execution began. Follow the returned run.id "
        "with get_run and get_run_report. Needs the run token scope."
    ),
    arguments_model=SkillSubmission,
    result_model=TaskSubmissionSchema,
    read_only=False,
)
def submit_task(
    context: ToolContext, arguments: SkillSubmission
) -> TaskSubmissionSchema:
    request = call_request(context)
    require(request, Action.RUN_JOBS)
    repository = repository_for_request(request, arguments.repository_id)
    try:
        result = submit_skill(repository, arguments, request.user, TaskOrigin.MCP)
    except RunRefused as error:
        raise ToolExecutionError(str(error)) from error
    return TaskSubmissionSchema(
        task_id=result.run.task_id,
        run=serialize_run(result.run),
        started=result.started,
    )


@register_tool(
    name="cancel_task",
    description="Cancel an ad hoc task before execution starts. Needs the run token scope.",
    arguments_model=RunArguments,
    result_model=RunResult,
    read_only=False,
)
def cancel_task(context: ToolContext, arguments: RunArguments) -> RunResult:
    request = call_request(context)
    require(request, Action.RUN_JOBS)
    run = run_for_request(request, arguments.run_id)
    try:
        canceled = cancel_queued_task(run)
    except RunRefused as error:
        raise ToolExecutionError(str(error)) from error
    return RunResult(run=serialize_run_detail(canceled))
