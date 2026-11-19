"""Repository tools: resync after a commit."""

from thin_mcp import ToolContext, ToolExecutionError

from secondshift.jobs.repository_sync import wait_for_sync
from secondshift.mcp_server.schemas import RepositoryArguments, RepositorySyncResult
from secondshift.mcp_server.tools_registry import call_request, register_tool
from secondshift.repositories.access import repository_for_request
from secondshift.repositories.dao import request_sync
from secondshift.repositories.serialization import serialize_repository_detail
from secondshift.workspaces.access import Action, require

# The worker runs the sync. A tool call waits this long for it.
SYNC_WAIT_SECONDS = 60


@register_tool(
    name="sync_repository",
    description=(
        "Read .secondshift.yaml and the skills again from the branch's newest "
        "commit. A broken manifest returns its errors. A sync that takes "
        "longer than a minute returns with syncing set. Call the tool again "
        "to wait for it. Syncing never grants access: new requests still "
        "wait for an owner. Needs a token with the run scope."
    ),
    arguments_model=RepositoryArguments,
    result_model=RepositorySyncResult,
    read_only=False,
)
def sync_repository(
    context: ToolContext, arguments: RepositoryArguments
) -> RepositorySyncResult:
    request = call_request(context)
    repository = repository_for_request(request, arguments.repository_id)
    require(request, Action.RESYNC_REPOSITORIES)
    request_sync(repository.pk)
    finished = wait_for_sync(repository.pk, SYNC_WAIT_SECONDS)
    repository.refresh_from_db()
    if finished and repository.last_sync_error:
        raise ToolExecutionError(repository.last_sync_error)
    detail = serialize_repository_detail(request, repository)
    return RepositorySyncResult(
        repository_id=detail.id,
        name=detail.full_name,
        revision=detail.revision,
        validation=detail.validation,
        jobs=detail.jobs,
        syncing=not finished,
    )
