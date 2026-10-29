"""Manifest tools: what a .secondshift.yaml can ask for, and a draft check."""

from dataclasses import asdict

from thin_mcp import EmptyToolArguments, ToolContext

from secondshift.connections.catalog import workspace_catalog
from secondshift.jobs.drafts import check_draft
from secondshift.jobs.manifest import ManifestError
from secondshift.jobs.needs import capability_needs
from secondshift.mcp_server.schemas import (
    BindingListResult,
    BindingSchema,
    BuiltInProviderSchema,
    DraftJobSchema,
    ManifestArguments,
    ManifestCheckResult,
    NeedJobSummary,
    NeedListResult,
    NeedSchema,
)
from secondshift.mcp_server.tools_registry import call_request, register_tool
from secondshift.repositories.access import repository_for_request
from secondshift.workspaces.access import current_workspace


@register_tool(
    name="list_bindings",
    description=(
        "List the bindings a .secondshift.yaml can name in this workspace, with "
        "the tools a job may request from each. Use a binding as `binding` and "
        "its tool names as `tools` under requires.mcps or requires.apis, or a "
        "notification binding under notify. Built-in GitHub and Sentry tools "
        "are listed even when not connected."
    ),
    arguments_model=EmptyToolArguments,
    result_model=BindingListResult,
    read_only=True,
)
def list_bindings(
    context: ToolContext, arguments: EmptyToolArguments
) -> BindingListResult:
    workspace = current_workspace(call_request(context))
    if workspace is None:
        return BindingListResult(bindings=[], built_in=[])
    bindings, built_ins = workspace_catalog(workspace)
    return BindingListResult(
        bindings=[BindingSchema(**asdict(item)) for item in bindings],
        built_in=[BuiltInProviderSchema(**asdict(item)) for item in built_ins],
    )


@register_tool(
    name="check_manifest",
    description=(
        "Check a draft .secondshift.yaml before committing it. Returns the "
        "error a sync would show, or each job's bindings with their status in "
        "this workspace: ready, missing, needs_approval or unavailable. Skill "
        "files are not read. Pass repository_id to count approvals its synced "
        "jobs already have."
    ),
    arguments_model=ManifestArguments,
    result_model=ManifestCheckResult,
    read_only=True,
)
def check_manifest(
    context: ToolContext, arguments: ManifestArguments
) -> ManifestCheckResult:
    request = call_request(context)
    repository = (
        repository_for_request(request, arguments.repository_id)
        if arguments.repository_id is not None
        else None
    )
    workspace = current_workspace(request)
    if workspace is None:
        return ManifestCheckResult(valid=False, error="No workspace.", jobs=[])
    try:
        jobs = check_draft(workspace, arguments.yaml, repository)
    except ManifestError as error:
        return ManifestCheckResult(valid=False, error=str(error), jobs=[])
    return ManifestCheckResult(
        valid=True,
        error="",
        jobs=[DraftJobSchema(**asdict(job)) for job in jobs],
    )


@register_tool(
    name="list_capability_needs",
    description=(
        "List what every job in the workspace has requested and what is still "
        "missing: connections to add and approvals an owner has to give."
    ),
    arguments_model=EmptyToolArguments,
    result_model=NeedListResult,
    read_only=True,
)
def list_capability_needs(
    context: ToolContext, arguments: EmptyToolArguments
) -> NeedListResult:
    workspace = current_workspace(call_request(context))
    if workspace is None:
        return NeedListResult(needs=[])
    return NeedListResult(
        needs=[
            NeedSchema(
                binding=need.binding,
                kind=need.kind,
                required=need.required,
                requested_tools=need.requested_tools,
                status=need.status,
                detail=need.detail,
                jobs=[
                    NeedJobSummary(
                        job_id=job.job_id,
                        job_name=job.job_name,
                        repository=job.repository,
                        required=job.required,
                        grant_status=job.grant_status,
                    )
                    for job in need.jobs
                ],
            )
            for need in capability_needs(workspace)
        ]
    )
