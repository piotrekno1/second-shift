"""Check a draft .secondshift.yaml against a workspace, before it is committed.

Nothing is saved. Skill files are not read, so a missing skill shows up only
when the committed manifest is synced. A binding is ready only when the same
job already has an approved grant that covers what the draft asks for.
Syncing never grants access, so anything new waits for an owner.
"""

from dataclasses import dataclass, field

import yaml

from secondshift.config.constants import MANIFEST_FILE
from secondshift.connections.bindings import binding_connections
from secondshift.connections.models import Connection
from secondshift.jobs.manifest import JobManifest, ManifestError, parse_manifest
from secondshift.jobs.models import CapabilityRequest, JobGrant
from secondshift.jobs.needs import NeedStatus, connection_problem
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace

NEW_ACCESS_DETAIL = "Connected. An owner approves it after the manifest is synced."
APPROVED_DETAIL = "Connected and already approved for this job."


@dataclass(frozen=True)
class DraftBinding:
    binding: str
    kind: str
    required: bool
    requested_tools: list[str]
    status: NeedStatus
    detail: str


@dataclass(frozen=True)
class DraftJob:
    key: str
    name: str
    schedule: str | None
    bindings: list[DraftBinding] = field(default_factory=list)


def requested(job: JobManifest) -> list[tuple[str, str, bool, list[str]]]:
    """Each binding the job asks for: binding, kind, required and tools."""
    return [
        *(
            (item.binding, CapabilityRequest.Kind.MCP, item.required, item.tools)
            for item in job.requires.mcps
        ),
        *(
            (item.binding, CapabilityRequest.Kind.API, item.required, item.tools)
            for item in job.requires.apis
        ),
        *(
            (item.binding, CapabilityRequest.Kind.NOTIFICATION, item.required, [])
            for item in job.notify
        ),
    ]


def approved_grant(
    repository: Repository | None,
    key: str,
    binding: str,
    kind: str,
    connection: Connection,
    tools: set[str],
) -> bool:
    if repository is None:
        return False
    request = (
        CapabilityRequest.objects.filter(
            job__repository=repository, job__key=key, binding=binding, kind=kind
        )
        .select_related("grant")
        .first()
    )
    grant = getattr(request, "grant", None) if request else None
    return bool(
        grant
        and grant.status == JobGrant.Status.APPROVED
        and grant.connection_id == connection.pk
        and tools <= set(grant.allowed_tools)
    )


def check_draft(
    workspace: Workspace, text: str, repository: Repository | None = None
) -> list[DraftJob]:
    """Each draft job's bindings and their status. Raises ManifestError."""
    try:
        manifest = parse_manifest(text)
    except (ValueError, yaml.YAMLError) as error:
        # The same wording a sync shows for the committed file.
        raise ManifestError(f"Invalid {MANIFEST_FILE}: {error}") from error
    connections = binding_connections(workspace.pk)
    repositories = [repository] if repository else []
    jobs = []
    for key, job in manifest.jobs.items():
        bindings = []
        for binding, kind, required, tools in requested(job):
            connection = connections.get(binding)
            problem = connection_problem(kind, connection, set(tools), repositories)
            if problem:
                status, detail = problem
            elif connection and approved_grant(
                repository, key, binding, kind, connection, set(tools)
            ):
                status, detail = "ready", APPROVED_DETAIL
            else:
                status, detail = "needs_approval", NEW_ACCESS_DETAIL
            bindings.append(
                DraftBinding(binding, kind, required, sorted(tools), status, detail)
            )
        jobs.append(DraftJob(key, job.name, job.schedule, bindings))
    return jobs
