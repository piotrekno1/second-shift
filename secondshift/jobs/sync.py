"""Reconcile a repository's jobs, capability requests and grants with its manifest.

Sync reads the existing rows once, creates new rows in bulk and saves only the
rows that changed. Its query count stays the same however many jobs there are.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from django.db import models, transaction
from django.utils import timezone

from secondshift.config.constants import MAX_SKILL_PREVIEW_CHARS
from secondshift.connections.models import Connection
from secondshift.jobs.automatic_grants import automatic_approval
from secondshift.jobs.manifest import (
    CapabilityManifest,
    JobManifest,
    NotificationManifest,
    RepositoryManifest,
    load_manifest,
    manifest_hash,
)
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.scheduling import next_occurrence
from secondshift.jobs.tool_rules import kept_rules
from secondshift.repositories.models import Repository


@dataclass(frozen=True)
class SyncedRequest:
    request: CapabilityRequest
    tools: list[str]
    # False when this sync created the request.
    existed: bool
    # The manifest asks for more tools than before, or for another kind.
    expanded: bool


def sync_manifest(repository: Repository, path: Path) -> list[Job]:
    """Sync from a manifest on disk. Skill paths resolve next to the manifest."""
    manifest = load_manifest(path)
    skills = {
        item.skill: skill_file.read_text()
        for item in manifest.jobs.values()
        if (skill_file := path.parent / item.skill).is_file()
    }
    return sync_jobs(repository, manifest, skills)


@transaction.atomic
def sync_jobs(
    repository: Repository,
    manifest: RepositoryManifest,
    skills: Mapping[str, str] | None = None,
) -> list[Job]:
    """`skills` maps each skill path to its text at the synced commit."""
    jobs = sync_job_rows(repository, manifest, skills or {})
    requests = sync_capability_requests(repository, jobs, manifest)
    sync_grants(repository, requests)
    repository.jobs.exclude(key__in=manifest.jobs).update(enabled=False)
    repository.last_synced_at = timezone.now()
    repository.save(update_fields=["last_synced_at", "updated_at"])
    return jobs


def assign(instance: models.Model, fields: Mapping[str, object]) -> list[str]:
    """Set the fields that differ and return their names."""
    changed = [
        name for name, value in fields.items() if getattr(instance, name) != value
    ]
    for name in changed:
        setattr(instance, name, fields[name])
    return changed


def save_changes(instance: models.Model, changed: list[str]) -> None:
    if changed:
        instance.save(update_fields=[*changed, "updated_at"])


def job_fields(
    repository: Repository, item: JobManifest, skill: str
) -> dict[str, object]:
    return {
        "workspace_id": repository.workspace_id,
        "name": item.name,
        "skill_path": item.skill,
        "skill_text": skill[:MAX_SKILL_PREVIEW_CHARS],
        "skill_truncated": len(skill) > MAX_SKILL_PREVIEW_CHARS,
        "cron": item.schedule or "",
        "timezone": item.timezone,
        "history": item.history,
        "config_hash": manifest_hash(item),
    }


def schedule(job: Job) -> list[str]:
    if not job.cron:
        # A job without a schedule is never on the scheduler.
        return assign(job, {"next_run_at": None, "enabled": False})
    if job.next_run_at is None:
        job.next_run_at = next_occurrence(job)
        return ["next_run_at"]
    return []


def sync_job_rows(
    repository: Repository, manifest: RepositoryManifest, skills: Mapping[str, str]
) -> list[Job]:
    existing = {job.key: job for job in repository.jobs.all()}
    jobs: list[Job] = []
    created: list[Job] = []
    for key, item in manifest.jobs.items():
        job = existing.get(key) or Job(key=key)
        job.repository = repository
        changed = assign(job, job_fields(repository, item, skills.get(item.skill, "")))
        changed += schedule(job)
        if job.pk is None:
            created.append(job)
        else:
            save_changes(job, changed)
        jobs.append(job)
    Job.objects.bulk_create(created)
    return jobs


def requested_capabilities(
    item: JobManifest,
) -> list[tuple[str, CapabilityManifest | NotificationManifest]]:
    return [
        *(
            (CapabilityRequest.Kind.MCP, capability)
            for capability in item.requires.mcps
        ),
        *(
            (CapabilityRequest.Kind.API, capability)
            for capability in item.requires.apis
        ),
        *(
            (CapabilityRequest.Kind.NOTIFICATION, notification)
            for notification in item.notify
        ),
    ]


def sync_capability_requests(
    repository: Repository, jobs: list[Job], manifest: RepositoryManifest
) -> list[SyncedRequest]:
    existing: dict[int, dict[str, CapabilityRequest]] = defaultdict(dict)
    for request in CapabilityRequest.objects.filter(
        job__repository=repository
    ).select_related("grant"):
        existing[request.job_id][request.binding] = request

    synced: list[SyncedRequest] = []
    created: list[CapabilityRequest] = []
    stale: list[int] = []
    for job, item in zip(jobs, manifest.jobs.values(), strict=True):
        current = existing.pop(job.pk, {})
        for kind, capability in requested_capabilities(item):
            tools = list(getattr(capability, "tools", []))
            fields = {
                "kind": kind,
                "requested_tools": tools,
                "required": capability.required,
            }
            request = current.pop(capability.binding, None)
            if request is None:
                request = CapabilityRequest(
                    job=job, binding=capability.binding, **fields
                )
                created.append(request)
                synced.append(SyncedRequest(request, tools, False, False))
                continue
            expanded = (
                not set(tools).issubset(request.requested_tools) or request.kind != kind
            )
            save_changes(request, assign(request, fields))
            synced.append(SyncedRequest(request, tools, True, expanded))
        # Bindings the manifest no longer asks for.
        stale += [request.pk for request in current.values()]

    CapabilityRequest.objects.bulk_create(created)
    if stale:
        CapabilityRequest.objects.filter(pk__in=stale).delete()
    return synced


def sync_grants(repository: Repository, requests: list[SyncedRequest]) -> None:
    """Grant matching connections, and send changed requests back for review.

    A waiting request is approved here when its connection does not require
    approval and allows every tool it asks for.
    """
    connections = {
        (connection.binding_key, connection.kind): connection
        for connection in Connection.objects.filter(
            workspace_id=repository.workspace_id
        ).exclude(binding_key="")
    }
    created: list[JobGrant] = []
    for synced in requests:
        request = synced.request
        grant = getattr(request, "grant", None) if synced.existed else None
        connection = connections.get((request.binding, request.kind))
        new = grant is None
        if grant is None:
            if connection is None:
                continue
            grant = JobGrant(
                capability_request=request,
                connection=connection,
                allowed_tools=sorted(synced.tools),
            )
        changed = assign(grant, review_fields(grant, synced)) if synced.existed else []
        if (
            grant.status == JobGrant.Status.PENDING
            and connection is not None
            and grant.connection_id in (None, connection.pk)
            and (fields := automatic_approval(request, connection, repository))
        ):
            changed += assign(grant, fields)
        if new:
            created.append(grant)
        else:
            save_changes(grant, changed)
    JobGrant.objects.bulk_create(created)


def review_fields(grant: JobGrant, synced: SyncedRequest) -> dict[str, object]:
    if synced.expanded:
        # New tools need a fresh approval.
        return {
            "status": JobGrant.Status.PENDING,
            "allowed_tools": sorted(synced.tools),
            # Kept so the owner reviews them again with the new tools.
            "tool_rules": kept_rules(grant.tool_rules, synced.tools),
            "reviewed_by_id": None,
            "reviewed_at": None,
        }
    # Tools the job stopped asking for are no longer allowed.
    allowed = sorted(set(grant.allowed_tools) & set(synced.tools))
    return {
        "allowed_tools": allowed,
        "tool_rules": kept_rules(grant.tool_rules, allowed),
    }
