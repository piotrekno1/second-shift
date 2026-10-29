"""Job tools: list jobs, read one, and start a run."""

from thin_mcp import ToolContext, ToolExecutionError

from secondshift.jobs.access import job_for_request
from secondshift.jobs.models import Job
from secondshift.jobs.readiness import readiness_problems
from secondshift.jobs.serialization import serialize_job_detail
from secondshift.mcp_server.schemas import (
    JobArguments,
    JobListArguments,
    JobListResult,
    JobResult,
    JobSummary,
    RunStartResult,
)
from secondshift.mcp_server.tools_registry import call_request, register_tool
from secondshift.runs.services import RunInProgress, RunRefused, start_manual_run
from secondshift.workspaces.access import Action, current_workspace, require


@register_tool(
    name="list_jobs",
    description=(
        "List the workspace's jobs with their schedule and whether Run now "
        "would start them. A job that is not ready says what blocks it."
    ),
    arguments_model=JobListArguments,
    result_model=JobListResult,
    read_only=True,
)
def list_jobs(context: ToolContext, arguments: JobListArguments) -> JobListResult:
    jobs = Job.objects.filter(
        workspace=current_workspace(call_request(context)),
        repository__removed_at__isnull=True,
    ).select_related("repository")
    if arguments.repository_id is not None:
        jobs = jobs.filter(repository_id=arguments.repository_id)
    if arguments.enabled is not None:
        jobs = jobs.filter(enabled=arguments.enabled)
    summaries = []
    for job in jobs.prefetch_related("capability_requests__grant__connection"):
        problems = readiness_problems(job)
        summaries.append(
            JobSummary(
                id=job.pk,
                key=job.key,
                name=job.name,
                repository_id=job.repository_id,
                repository=job.repository.full_name,
                schedule=job.cron,
                timezone=job.timezone,
                enabled=job.enabled,
                ready=not problems,
                blocked_by=problems,
            )
        )
    return JobListResult(jobs=summaries)


@register_tool(
    name="get_job",
    description=(
        "Read one job: its skill path, requested capabilities and their "
        "approval, limits and recent runs."
    ),
    arguments_model=JobArguments,
    result_model=JobResult,
    read_only=True,
)
def get_job(context: ToolContext, arguments: JobArguments) -> JobResult:
    job = job_for_request(call_request(context), arguments.job_id)
    problems = readiness_problems(job)
    return JobResult(
        job=serialize_job_detail(job, False), ready=not problems, blocked_by=problems
    )


@register_tool(
    name="run_job",
    description=(
        "Start a run of an approved job, like Run now. While the job already "
        "has a queued or running run, no new run starts and that run is "
        "returned. Follow it with get_run. Needs a token with the run scope."
    ),
    arguments_model=JobArguments,
    result_model=RunStartResult,
    read_only=False,
)
def run_job(context: ToolContext, arguments: JobArguments) -> RunStartResult:
    request = call_request(context)
    job = job_for_request(request, arguments.job_id)
    require(request, Action.RUN_JOBS)
    try:
        run = start_manual_run(job)
    except RunInProgress as error:
        return RunStartResult(
            run_id=error.run.pk,
            status=error.run.status,
            started=False,
            message=str(error),
        )
    except RunRefused as error:
        raise ToolExecutionError(str(error)) from error
    return RunStartResult(
        run_id=run.pk, status=run.status, started=True, message="The run started."
    )
