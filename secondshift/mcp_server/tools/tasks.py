"""Submit a repository skill without installing a reusable scheduled job."""

from thin_mcp import ToolContext, ToolExecutionError

from secondshift.config.constants import TaskOrigin
from secondshift.mcp_server.tools_registry import call_request, register_tool
from secondshift.repositories.access import repository_for_request
from secondshift.runs.schemas import TaskSubmissionSchema
from secondshift.runs.serialization import serialize_run
from secondshift.runs.services import RunRefused
from secondshift.runs.submissions import SkillSubmission, submit_skill
from secondshift.workspaces.access import Action, require


@register_tool(
    name="submit_task",
    description=(
        "Submit a committed repository skill for one execution, without creating a job. "
        "Provide a definition (name, skill, logical requires/notify), optional input "
        "and branch or full commit revision. Defaults to the repository's followed "
        "branch and pins the accepted commit. Reuse the same idempotency_key when "
        "retrying the same request. Connections must allow automatic approval. "
        "One ad hoc task per repository may be active. Follow the returned run.id "
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
