"""Repository tools: resync after a commit."""

from thin_mcp import ToolContext, ToolExecutionError

from secondshift.mcp_server.schemas import RepositoryArguments, RepositorySyncResult
from secondshift.mcp_server.tools_registry import call_request, register_tool
from secondshift.repositories.access import repository_for_request
from secondshift.repositories.serialization import serialize_repository_detail
from secondshift.repositories.sync import SyncError, resync
from secondshift.workspaces.access import Action, require


@register_tool(
    name="sync_repository",
    description=(
        "Read .secondshift.yaml and the skills again from the branch's newest "
        "commit. A broken manifest returns its errors. Syncing never grants "
        "access: new requests still wait for an owner. Needs a token with the "
        "run scope."
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
    try:
        repository = resync(repository)
    except SyncError as error:
        raise ToolExecutionError(str(error)) from error
    detail = serialize_repository_detail(request, repository)
    return RepositorySyncResult(
        repository_id=detail.id,
        name=detail.full_name,
        revision=detail.revision,
        validation=detail.validation,
        jobs=detail.jobs,
    )
