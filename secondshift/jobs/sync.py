from pathlib import Path

from django.db import transaction
from django.utils import timezone

from secondshift.connections.models import Connection
from secondshift.jobs.manifest import (
    JobManifest,
    RepositoryManifest,
    load_manifest,
    manifest_hash,
)
from secondshift.jobs.models import CapabilityRequest, Job, JobGrant
from secondshift.jobs.scheduling import next_occurrence
from secondshift.repositories.models import Repository


def sync_manifest(repository: Repository, path: Path) -> list[Job]:
    return sync_jobs(repository, load_manifest(path))


@transaction.atomic
def sync_jobs(repository: Repository, manifest: RepositoryManifest) -> list[Job]:
    synced: list[Job] = []
    active_keys = set(manifest.jobs)
    for key, item in manifest.jobs.items():
        job, _ = Job.objects.update_or_create(
            repository=repository,
            key=key,
            defaults={
                "workspace": repository.workspace,
                "name": item.name,
                "skill_path": item.skill,
                "cron": item.schedule or "",
                "timezone": item.timezone,
                "config_hash": manifest_hash(item),
            },
        )
        if not job.cron:
            # A job without a schedule is never on the scheduler.
            job.next_run_at = None
            job.enabled = False
            job.save(update_fields=["next_run_at", "enabled", "updated_at"])
        elif job.next_run_at is None:
            job.next_run_at = next_occurrence(job)
            job.save(update_fields=["next_run_at", "updated_at"])
        sync_capability_requests(job, item)
        synced.append(job)

    repository.jobs.exclude(key__in=active_keys).update(enabled=False)
    repository.last_synced_at = timezone.now()
    repository.save(update_fields=["last_synced_at", "updated_at"])
    return synced


def sync_capability_requests(job: Job, item: JobManifest) -> None:
    requested = (
        [(CapabilityRequest.Kind.MCP, capability) for capability in item.requires.mcps]
        + [
            (CapabilityRequest.Kind.API, capability)
            for capability in item.requires.apis
        ]
        + [
            (CapabilityRequest.Kind.NOTIFICATION, notification)
            for notification in item.notify
        ]
    )
    active_bindings: set[str] = set()
    for kind, capability in requested:
        active_bindings.add(capability.binding)
        tools = list(getattr(capability, "tools", []))
        existing = CapabilityRequest.objects.filter(
            job=job,
            binding=capability.binding,
        ).first()
        previous_tools = set(existing.requested_tools) if existing else set()
        previous_kind = existing.kind if existing else None
        request, _ = CapabilityRequest.objects.update_or_create(
            job=job,
            binding=capability.binding,
            defaults={
                "kind": kind,
                "requested_tools": tools,
                "required": capability.required,
            },
        )
        expanded = not set(tools).issubset(previous_tools) or previous_kind != kind
        grant = JobGrant.objects.filter(capability_request=request).first()
        if not grant:
            connection = Connection.objects.filter(
                workspace=job.workspace,
                binding_key=request.binding,
                kind=request.kind,
            ).first()
            if connection:
                grant = JobGrant.objects.create(
                    capability_request=request,
                    connection=connection,
                    allowed_tools=tools,
                )
        if existing and expanded and grant:
            grant.status = JobGrant.Status.PENDING
            grant.allowed_tools = tools
            grant.reviewed_by = None
            grant.reviewed_at = None
            grant.save(
                update_fields=[
                    "status",
                    "allowed_tools",
                    "reviewed_by",
                    "reviewed_at",
                    "updated_at",
                ]
            )
        elif existing and grant:
            grant.allowed_tools = sorted(set(grant.allowed_tools) & set(tools))
            grant.save(update_fields=["allowed_tools", "updated_at"])

    job.capability_requests.exclude(binding__in=active_bindings).delete()
